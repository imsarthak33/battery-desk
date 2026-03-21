"""
scrapers/base_scraper.py
─────────────────────────
Base class for all scrapers.
Handles: user-agent rotation, request throttling, retry with backoff,
         Playwright browser management, response validation.
"""

import logging
import random
import time
from abc import ABC, abstractmethod
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# ── User-Agent Pool ────────────────────────────────────────────────────────────
# Rotate between real browser UAs to avoid bot detection
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
]

# ── Common headers that mimic a real browser ───────────────────────────────────
def get_browser_headers(referer: str = "https://www.google.com") -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Cache-Control": "max-age=0",
        "Referer": referer,
    }


def make_session(retries: int = 3, backoff: float = 1.5) -> requests.Session:
    """
    Create a requests.Session with automatic retry on transient errors.
    """
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def human_delay(min_sec: float = 1.0, max_sec: float = 3.5):
    """Random sleep to mimic human browsing cadence."""
    time.sleep(random.uniform(min_sec, max_sec))


class BaseScraper(ABC):
    """
    Abstract base for all metal price scrapers.
    Subclasses must implement `scrape()`.
    """

    SOURCE_NAME: str = "unknown"
    BASE_URL: str = ""

    def __init__(self, timeout: int = 8):
        self.timeout = timeout
        self.session = make_session(retries=1, backoff=0.5)
        self.logger = logging.getLogger(self.__class__.__name__)

    def get(self, url: str, params: dict = None, extra_headers: dict = None) -> Optional[requests.Response]:
        """HTTP GET with browser headers and error handling."""
        headers = get_browser_headers(referer=self.BASE_URL)
        if extra_headers:
            headers.update(extra_headers)
        try:
            resp = self.session.get(url, headers=headers, params=params, timeout=self.timeout)
            if resp.status_code in [403, 401]:
                self.logger.warning(f"Blocked from {url} — bot detection triggered")
                return None
            if resp.status_code == 429:
                self.logger.warning(f"429 Rate Limited from {url}")
                return None
            resp.raise_for_status()
            return resp
        except requests.exceptions.ConnectionError:
            self.logger.error(f"Connection failed: {url}")
            return None
        except requests.exceptions.Timeout:
            self.logger.error(f"Timeout: {url}")
            return None
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Request error {url}: {e}")
            return None

    @abstractmethod
    def scrape(self) -> dict:
        """
        Returns dict like:
        {
            "nickel":   {"price": 16450.0, "unit": "USD/tonne", "source": "..."},
            "cobalt":   {"price": 25800.0, "unit": "USD/tonne", "source": "..."},
            ...
            "_meta": {"source": "...", "timestamp": "...", "success": True}
        }
        """
        pass

    def safe_float(self, raw: str) -> Optional[float]:
        """Parse price strings like '16,450.50' or '$16.4K' safely."""
        if not raw:
            return None
        try:
            cleaned = raw.strip().replace(",", "").replace("$", "").replace("₹", "").replace(" ", "")
            # Handle 'K' suffix (thousands)
            if cleaned.upper().endswith("K"):
                return float(cleaned[:-1]) * 1000
            return float(cleaned)
        except (ValueError, AttributeError):
            self.logger.debug(f"Could not parse price: '{raw}'")
            return None
