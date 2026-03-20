"""
tools/forex_fetcher.py
──────────────────────
Fetches live USD/INR exchange rate.
Sources:
  Tier 1: exchangerate-api.com  (free: 1500 req/month)
  Tier 2: open.er-api.com       (completely free, no key)
  Tier 3: Hardcoded fallback    (must be updated monthly)
"""

import logging
import os
from datetime import datetime
from typing import Optional

import requests

from config import EXCHANGE_RATE_KEY, FALLBACK_USD_INR

logger = logging.getLogger(__name__)


class ForexFetcher:

    def __init__(self):
        self._cached_rate: Optional[float] = None
        self._cached_at: Optional[datetime] = None
        self.session = requests.Session()

    def get_usd_inr(self) -> dict:
        """
        Returns:
        {
            "rate": 84.12,
            "source": "exchangerate-api.com",
            "timestamp": "...",
            "stale": False
        }
        """
        # Use in-memory cache for same run (don't hammer forex API)
        if self._cached_rate and self._cached_at:
            age_min = (datetime.utcnow() - self._cached_at).total_seconds() / 60
            if age_min < 60:  # reuse if <1hr old within same process run
                return {
                    "rate": self._cached_rate,
                    "source": "in-process cache",
                    "timestamp": self._cached_at.isoformat(),
                    "stale": False,
                }

        result = None

        if EXCHANGE_RATE_KEY:
            result = self._fetch_exchangerate_api()

        if result is None:
            result = self._fetch_open_er_api()

        if result is None:
            logger.error("All forex APIs failed. Using hardcoded fallback rate.")
            result = {
                "rate": FALLBACK_USD_INR,
                "source": "HARDCODED_FALLBACK",
                "timestamp": datetime.utcnow().isoformat(),
                "stale": True,
            }

        self._cached_rate = result["rate"]
        self._cached_at = datetime.utcnow()
        return result

    def _fetch_exchangerate_api(self) -> Optional[dict]:
        try:
            url = f"https://v6.exchangerate-api.com/v6/{EXCHANGE_RATE_KEY}/pair/USD/INR"
            resp = self.session.get(url, timeout=8)
            resp.raise_for_status()
            data = resp.json()
            if data.get("result") == "success":
                return {
                    "rate": data["conversion_rate"],
                    "source": "exchangerate-api.com",
                    "timestamp": datetime.utcnow().isoformat(),
                    "stale": False,
                }
        except Exception as e:
            logger.warning(f"exchangerate-api failed: {e}")
        return None

    def _fetch_open_er_api(self) -> Optional[dict]:
        """
        open.er-api.com — completely free, no key, generous limits.
        """
        try:
            url = "https://open.er-api.com/v6/latest/USD"
            resp = self.session.get(url, timeout=8)
            resp.raise_for_status()
            data = resp.json()
            if data.get("result") == "success":
                inr_rate = data["rates"].get("INR")
                if inr_rate:
                    return {
                        "rate": float(inr_rate),
                        "source": "open.er-api.com (free)",
                        "timestamp": datetime.utcnow().isoformat(),
                        "stale": False,
                    }
        except Exception as e:
            logger.warning(f"open.er-api failed: {e}")
        return None


_fetcher = ForexFetcher()


def get_usd_inr_rate() -> dict:
    return _fetcher.get_usd_inr()
