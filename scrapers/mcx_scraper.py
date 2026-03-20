"""
scrapers/mcx_scraper.py
────────────────────────
Scrapes live commodity prices from MCX India (Multi Commodity Exchange).
mcxindia.com has a public JSON API used by their own website — no auth needed.

Covers metals traded on MCX:
  Nickel   → MCX Nickel (INR per kg, active contract)
  Lead     → MCX Lead   (INR per kg)
  Aluminum → MCX Aluminium (INR per kg)
  Zinc     → MCX Zinc (INR per kg)

INR prices are converted to USD/tonne using live forex rate for consistency.
"""

import json
import re
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper, human_delay


class MCXScraper(BaseScraper):
    """
    MCX India — the best free source for Indian base metal prices.
    MCX's own website fetches data from internal JSON endpoints.
    """

    SOURCE_NAME = "mcxindia.com"
    BASE_URL = "https://www.mcxindia.com"

    # MCX symbol codes (used in their internal API)
    MCX_SYMBOLS = {
        "nickel":    "NICKEL",
        "lead":      "LEAD",
        "aluminum":  "ALUMINIUM",
        "zinc":      "ZINC",
        "copper":    "COPPER",
    }

    # MCX API endpoint (discovered via browser DevTools on mcxindia.com)
    # This endpoint powers the live ticker on their homepage
    QUOTE_API = "https://www.mcxindia.com/backpage.aspx/GetQuotes"
    MARKET_API = "https://www.mcxindia.com/BackPage.aspx/GetBestMarketData"

    def __init__(self, metals: list[str] = None, usd_inr_rate: float = 84.0):
        super().__init__(timeout=15)
        self.metals = [m for m in (metals or ["nickel", "lead"]) if m in self.MCX_SYMBOLS]
        self.usd_inr_rate = usd_inr_rate

    def scrape(self) -> dict:
        results = {}
        success_count = 0

        # Try the MCX JSON API first
        api_data = self._fetch_mcx_api()

        for metal in self.metals:
            symbol = self.MCX_SYMBOLS[metal]
            inr_per_kg = None

            if api_data and symbol in api_data:
                inr_per_kg = api_data[symbol]
                self.logger.info(f"  MCX API: {metal} = ₹{inr_per_kg:.2f}/kg")
            else:
                # Fallback: scrape HTML market data page
                inr_per_kg = self._scrape_html(symbol)
                if inr_per_kg:
                    self.logger.info(f"  MCX HTML: {metal} = ₹{inr_per_kg:.2f}/kg")

            if inr_per_kg:
                # Convert INR/kg → USD/tonne for standardization
                usd_per_tonne = (inr_per_kg / self.usd_inr_rate) * 1000
                results[metal] = {
                    "price": round(usd_per_tonne, 2),
                    "price_inr_per_kg": round(inr_per_kg, 2),
                    "unit": "USD/tonne",
                    "original_unit": "INR/kg",
                    "source": self.SOURCE_NAME,
                    "forex_used": self.usd_inr_rate,
                }
                success_count += 1
            else:
                self.logger.warning(f"  MCX: failed to fetch {metal}")

            human_delay(1.0, 2.5)

        results["_meta"] = {
            "source": self.SOURCE_NAME,
            "timestamp": datetime.utcnow().isoformat(),
            "success": success_count > 0,
            "metals_fetched": success_count,
            "note": "MCX prices are INR/kg converted to USD/tonne using live forex",
        }
        return results

    def _fetch_mcx_api(self) -> dict:
        """
        Calls MCX's internal quote API used by their own ticker widget.
        Returns: {"NICKEL": 1654.50, "LEAD": 184.30, ...}  (INR per kg)
        """
        try:
            # MCX uses POST with JSON body for their internal API
            url = self.QUOTE_API
            payload = {"strExpiryDate": "", "strSymbol": ""}

            resp = self.session.post(
                url,
                json=payload,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0",
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": "https://www.mcxindia.com/market-data/commodity-futures",
                    "Origin": "https://www.mcxindia.com",
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            # MCX returns nested structure: {"d": [{"Symbol": "NICKEL", "LastTradePrice": "1,654.50"}, ...]}
            result = {}
            items = data.get("d", data if isinstance(data, list) else [])
            for item in items:
                sym = item.get("Symbol", "").upper()
                if sym in self.MCX_SYMBOLS.values():
                    ltp = item.get("LastTradePrice") or item.get("LTP") or item.get("Last")
                    if ltp:
                        price = self.safe_float(str(ltp))
                        if price and price > 0:
                            result[sym] = price
            return result

        except json.JSONDecodeError as e:
            self.logger.warning(f"MCX API JSON parse error: {e}")
        except Exception as e:
            self.logger.warning(f"MCX API failed: {e}")
        return {}

    def _scrape_html(self, symbol: str) -> Optional[float]:
        """
        Fallback: scrape the MCX market data page for a specific commodity.
        """
        url = f"{self.BASE_URL}/market-data/futures-market/live-market"
        resp = self.get(url, extra_headers={"Referer": self.BASE_URL})
        if not resp:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        # MCX renders data in a table. Find row where symbol column matches.
        rows = soup.find_all("tr")
        for row in rows:
            cells = row.find_all(["td", "th"])
            cell_texts = [c.get_text(strip=True) for c in cells]
            if any(symbol in t.upper() for t in cell_texts):
                # The LTP (Last Traded Price) is typically the 4th or 5th column
                for text in cell_texts:
                    val = self.safe_float(text)
                    if val and 50 < val < 100000:  # INR/kg sanity range
                        return val

        # Also try JSON embedded in page scripts
        script_pattern = re.compile(
            rf'"Symbol"\s*:\s*"{symbol}".*?"LastTradePrice"\s*:\s*"?([\d,\.]+)',
            re.IGNORECASE | re.DOTALL,
        )
        match = script_pattern.search(resp.text)
        if match:
            return self.safe_float(match.group(1))

        return None


class MCXHistoricalScraper(BaseScraper):
    """
    Scrapes MCX historical price data for trend analysis.
    Uses the MCX historical data page (no auth required).
    """

    SOURCE_NAME = "mcxindia.com (historical)"
    BASE_URL = "https://www.mcxindia.com"

    def __init__(self, symbol: str, days: int = 30):
        super().__init__()
        self.symbol = symbol.upper()
        self.days = days

    def scrape(self) -> dict:
        """Returns list of {date, close_price_inr} for trend analysis."""
        try:
            url = f"{self.BASE_URL}/backpage.aspx/GetCommodityHistoricalData"
            payload = {
                "strSymbol": self.symbol,
                "strFromDate": "",
                "strToDate": "",
                "strInterval": "D",  # Daily
            }
            resp = self.session.post(url, json=payload, timeout=15,
                                     headers={"X-Requested-With": "XMLHttpRequest",
                                              "Content-Type": "application/json"})
            data = resp.json()
            history = []
            for row in data.get("d", []):
                history.append({
                    "date": row.get("Date", ""),
                    "close": self.safe_float(str(row.get("Close", "0"))),
                })

            return {
                "symbol": self.symbol,
                "history": history[-self.days:],
                "_meta": {"source": self.SOURCE_NAME, "timestamp": datetime.utcnow().isoformat()},
            }
        except Exception as e:
            self.logger.error(f"MCX historical scrape failed: {e}")
            return {"symbol": self.symbol, "history": [], "_meta": {"source": self.SOURCE_NAME}}
