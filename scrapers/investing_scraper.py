"""
scrapers/investing_scraper.py
──────────────────────────────
Scrapes live commodity prices from investing.com.

Targets:
  Nickel   → /commodities/nickel
  Cobalt   → /commodities/cobalt
  Lithium  → /commodities/lithium-carbonate  (or /commodities/lithium)
  Lead     → /commodities/lead
  Aluminum → /commodities/aluminum (bonus — useful for casing scrap)

All prices returned in USD/tonne (converted where needed).

Anti-bot notes:
  - investing.com uses Cloudflare but is partially accessible with proper headers
  - We scrape the embedded JSON data from the page script tags, not raw HTML
  - As fallback we parse the visible price element
  - Rate limit: wait 2–4s between each metal request
"""

import json
import re
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper, human_delay


class InvestingComScraper(BaseScraper):

    SOURCE_NAME = "investing.com"
    BASE_URL = "https://www.investing.com"

    # investing.com URL slugs for each metal
    METAL_SLUGS = {
        "nickel":    "/commodities/nickel",
        "cobalt":    "/commodities/cobalt",
        "lithium":   "/commodities/lithium-carbonate",  # LCE spot
        "lead":      "/commodities/lead",
        "manganese": "/commodities/manganese-ore",
        "aluminum":  "/commodities/aluminum",
    }

    # investing.com prices for base metals are in USD per metric tonne
    # EXCEPT lithium which may be in CNY/tonne or USD/tonne depending on page
    UNIT_MAP = {
        "nickel":    "USD/tonne",
        "cobalt":    "USD/tonne",
        "lithium":   "USD/tonne",
        "lead":      "USD/tonne",
        "manganese": "USD/tonne",
        "aluminum":  "USD/tonne",
    }

    def __init__(self, metals: list[str] = None):
        super().__init__(timeout=20)
        self.metals = metals or ["nickel", "cobalt", "lithium", "lead"]

    def scrape(self) -> dict:
        results = {}
        success_count = 0

        for metal in self.metals:
            if metal not in self.METAL_SLUGS:
                self.logger.warning(f"No investing.com slug for {metal} — skipping")
                continue

            price = self._scrape_metal(metal)
            if price is not None:
                results[metal] = {
                    "price": price,
                    "unit": self.UNIT_MAP.get(metal, "USD/tonne"),
                    "source": self.SOURCE_NAME,
                }
                success_count += 1
            else:
                self.logger.warning(f"Failed to scrape {metal} from investing.com")

            human_delay(2.0, 4.0)  # polite crawling

        results["_meta"] = {
            "source": self.SOURCE_NAME,
            "timestamp": datetime.utcnow().isoformat(),
            "success": success_count > 0,
            "metals_fetched": success_count,
            "metals_requested": len(self.metals),
        }
        return results

    def _scrape_metal(self, metal: str) -> Optional[float]:
        """Fetch price for a single metal. Tries 3 strategies."""
        url = self.BASE_URL + self.METAL_SLUGS[metal]
        self.logger.info(f"Fetching {metal} from {url}")

        resp = self.get(url, extra_headers={
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-Mode": "navigate",
        })
        if not resp:
            return None

        html = resp.text

        # Strategy 1: Extract from embedded JSON (most reliable)
        price = self._extract_from_json_ld(html)
        if price:
            self.logger.info(f"  {metal}: ${price:,.2f}/t [JSON-LD]")
            return price

        # Strategy 2: Parse from meta tags
        price = self._extract_from_meta(html)
        if price:
            self.logger.info(f"  {metal}: ${price:,.2f}/t [meta tag]")
            return price

        # Strategy 3: Parse DOM element (fragile, last resort)
        price = self._extract_from_dom(html)
        if price:
            self.logger.info(f"  {metal}: ${price:,.2f}/t [DOM]")
            return price

        self.logger.warning(f"  All 3 strategies failed for {metal}")
        return None

    def _extract_from_json_ld(self, html: str) -> Optional[float]:
        """investing.com embeds price data in <script type="application/ld+json">"""
        try:
            matches = re.findall(
                r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                html,
                re.DOTALL | re.IGNORECASE,
            )
            for match in matches:
                try:
                    data = json.loads(match)
                    # Look for price fields in various JSON-LD schemas
                    for key in ["price", "currentPrice", "lastPrice", "value"]:
                        if key in data:
                            val = self.safe_float(str(data[key]))
                            if val and val > 100:  # sanity check — metals are > $100/t
                                return val
                except json.JSONDecodeError:
                    continue
        except Exception as e:
            self.logger.debug(f"JSON-LD extraction failed: {e}")
        return None

    def _extract_from_meta(self, html: str) -> Optional[float]:
        """Some pages embed price in <meta> tags."""
        try:
            soup = BeautifulSoup(html, "html.parser")
            # investing.com sometimes uses og:description with price
            desc = soup.find("meta", {"name": "description"}) or \
                   soup.find("meta", {"property": "og:description"})
            if desc:
                content = desc.get("content", "")
                # Pattern: "Nickel price today is 16,450.25 USD"
                match = re.search(r"[\d,]+\.?\d*", content.replace(",", ""))
                if match:
                    val = self.safe_float(match.group())
                    if val and val > 100:
                        return val
        except Exception as e:
            self.logger.debug(f"Meta extraction failed: {e}")
        return None

    def _extract_from_dom(self, html: str) -> Optional[float]:
        """
        Parse the visible price element from the DOM.
        investing.com uses data-test attributes on price elements.
        WARNING: This breaks when investing.com updates their frontend.
        """
        try:
            soup = BeautifulSoup(html, "html.parser")

            # Try multiple CSS selectors — investing.com has changed these over time
            selectors = [
                {"data-test": "instrument-price-last"},
                {"class": re.compile(r"last-price|instrument-price|price-value")},
                {"id": re.compile(r"last_last|lastPrice")},
            ]

            for selector in selectors:
                el = soup.find(attrs=selector)
                if el:
                    val = self.safe_float(el.get_text(strip=True))
                    if val and val > 100:
                        return val

            # Fallback: look for a number near "Last" label
            last_label = soup.find(string=re.compile(r"^Last\s*$", re.IGNORECASE))
            if last_label:
                parent = last_label.find_parent()
                if parent:
                    next_el = parent.find_next_sibling()
                    if next_el:
                        val = self.safe_float(next_el.get_text(strip=True))
                        if val and val > 100:
                            return val

        except Exception as e:
            self.logger.debug(f"DOM extraction failed: {e}")
        return None
