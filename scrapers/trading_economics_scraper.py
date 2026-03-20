"""
scrapers/trading_economics_scraper.py
──────────────────────────────────────
Fetches commodity prices from TradingEconomics.

TradingEconomics has a semi-open guest API (rate-limited but no key needed)
AND an HTML page we can scrape as fallback.

Covers:
  Nickel       → /commodity/nickel
  Cobalt       → /commodity/cobalt
  Lithium      → /commodity/lithium
  Lead         → /commodity/lead
  Manganese    → /commodity/manganese-ore

All prices in USD/tonne.
"""

import json
import re
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper, human_delay


class TradingEconomicsScraper(BaseScraper):

    SOURCE_NAME = "tradingeconomics.com"
    BASE_URL = "https://tradingeconomics.com"

    # TE commodity slugs
    COMMODITY_SLUGS = {
        "nickel":    "nickel",
        "cobalt":    "cobalt",
        "lithium":   "lithium",
        "lead":      "lead",
        "manganese": "manganese-ore",
    }

    # TE API endpoint (guest access, no key)
    API_BASE = "https://api.tradingeconomics.com/commodity"

    def __init__(self, metals: list[str] = None):
        super().__init__(timeout=15)
        self.metals = metals or ["nickel", "cobalt", "lithium", "lead"]

    def scrape(self) -> dict:
        results = {}
        success_count = 0

        # First try the guest JSON API (more reliable, returns structured data)
        api_results = self._try_guest_api()
        if api_results:
            results.update(api_results)
            success_count = len([k for k in results if not k.startswith("_")])

        # For any metals not yet fetched, try HTML scraping
        missing = [m for m in self.metals if m not in results]
        for metal in missing:
            price = self._scrape_html(metal)
            if price is not None:
                results[metal] = {
                    "price": price,
                    "unit": "USD/tonne",
                    "source": f"{self.SOURCE_NAME} (HTML)",
                }
                success_count += 1
            human_delay(1.5, 3.0)

        results["_meta"] = {
            "source": self.SOURCE_NAME,
            "timestamp": datetime.utcnow().isoformat(),
            "success": success_count > 0,
            "metals_fetched": success_count,
        }
        return results

    def _try_guest_api(self) -> dict:
        """
        TradingEconomics has a guest API endpoint that works without a key,
        but is rate-limited (~10 req/hour per IP).
        Returns: {metal: {price, unit, source}} or {}
        """
        results = {}
        try:
            # Fetch all commodity data in one call
            url = f"{self.API_BASE}?c=guest:guest"
            resp = self.get(url, extra_headers={"Accept": "application/json"})
            if not resp:
                return {}

            data = resp.json()
            if not isinstance(data, list):
                self.logger.warning("TE API returned unexpected format")
                return {}

            # Build lookup: commodity name → price
            # TE returns: [{"Symbol": "NICKEL", "Last": 16450.0, "Unit": "USD/T"}, ...]
            te_name_map = {
                "NICKEL":       "nickel",
                "COBALT":       "cobalt",
                "LITHIUM":      "lithium",
                "LEAD":         "lead",
                "MANGANESE ORE": "manganese",
            }

            for item in data:
                symbol = str(item.get("Symbol", "")).upper()
                metal = te_name_map.get(symbol)
                if metal and metal in self.metals:
                    raw_price = item.get("Last") or item.get("Price") or item.get("Value")
                    if raw_price:
                        price = float(raw_price)
                        # TE sometimes returns prices per lb — convert if suspiciously low
                        if price < 50:  # likely per lb
                            price = price * 2204.62  # lb → tonne
                        results[metal] = {
                            "price": round(price, 2),
                            "unit": item.get("Unit", "USD/tonne"),
                            "source": f"{self.SOURCE_NAME} (API)",
                            "last_update": item.get("LastUpdate", ""),
                        }
                        self.logger.info(f"  TE API: {metal} = ${price:,.2f}")

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            self.logger.warning(f"TE API parse error: {e}")
        except Exception as e:
            self.logger.warning(f"TE API failed: {e}")

        return results

    def _scrape_html(self, metal: str) -> Optional[float]:
        """Scrape commodity page HTML as fallback."""
        slug = self.COMMODITY_SLUGS.get(metal)
        if not slug:
            return None

        url = f"{self.BASE_URL}/commodity/{slug}"
        self.logger.info(f"Scraping TE HTML for {metal}: {url}")

        resp = self.get(url)
        if not resp:
            return None

        html = resp.text
        soup = BeautifulSoup(html, "html.parser")

        # Strategy 1: TE embeds data in a <script> with window.teData
        price = self._extract_te_script_data(html)
        if price:
            self.logger.info(f"  TE HTML script: {metal} = ${price:,.2f}")
            return price

        # Strategy 2: Parse the price display element
        # TE uses <span id="p"> or similar for the last price
        price_selectors = [
            {"id": "p"},
            {"class": re.compile(r"te-commodity-price|lastPrice|price-current")},
            {"data-value": True},
        ]
        for selector in price_selectors:
            el = soup.find(attrs=selector)
            if el:
                val = self.safe_float(el.get("data-value") or el.get_text(strip=True))
                if val and val > 50:
                    return val

        # Strategy 3: Search for price pattern in page text near metal name
        text = soup.get_text()
        pattern = rf"{metal}[^\d]{{0,50}}([\d,]+\.?\d*)"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            val = self.safe_float(match.group(1))
            if val and 100 < val < 1_000_000:  # sanity bounds
                return val

        self.logger.warning(f"  TE HTML: all strategies failed for {metal}")
        return None

    def _extract_te_script_data(self, html: str) -> Optional[float]:
        """
        TradingEconomics often inlines a JS object like:
        window.dataLayer = [...{"commodityPrice": 16450.25}...]
        """
        try:
            # Look for numeric price value near common TE variable names
            patterns = [
                r'"currentPrice"\s*:\s*([\d.]+)',
                r'"last"\s*:\s*([\d.]+)',
                r'"price"\s*:\s*([\d.]+)',
                r'"Last"\s*:\s*([\d.]+)',
            ]
            for pattern in patterns:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    val = self.safe_float(match.group(1))
                    if val and 100 < val < 10_000_000:
                        return val
        except Exception as e:
            self.logger.debug(f"TE script extraction failed: {e}")
        return None
