"""
tools/price_fetcher.py
─────────────────────
Fetches live LME / spot metal prices with a multi-tier fallback chain.

Tier 1: FREE WEB SCRAPING  — Google (via Serper) + Trading Economics guest API
Tier 2: metalpriceapi.com  — structured JSON (100 free req/month, some metals paid-only)
Tier 3: Cached prices file — last known good prices
Tier 4: Hardcoded fallback — approximate estimates, updated manually

Returns prices in USD per TONNE (converts to per-kg in calculator.py).
"""

import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

from config import METAL_PRICE_API_KEY, LME_SYMBOLS

logger = logging.getLogger(__name__)

CACHE_FILE = Path(__file__).parent.parent / "cache" / "last_known_prices.json"
CACHE_FILE.parent.mkdir(exist_ok=True)

# Approximate fallback prices (USD/tonne) — update monthly
HARDCODED_FALLBACK = {
    "nickel":    16800.0,
    "cobalt":    26000.0,
    "lithium":   13000.0,   # Lithium Carbonate equivalent
    "manganese":  1900.0,
    "lead":       2050.0,
}

# Trading Economics commodity search terms
TE_COMMODITY_MAP = {
    "nickel":    "nickel",
    "cobalt":    "cobalt",
    "lithium":   "lithium",
    "manganese": "manganese",
    "lead":      "lead",
}


class MetalPriceFetcher:
    """
    Fetches metal spot prices with a multi-source fallback chain.
    Always returns a dict with prices + a 'source' and 'timestamp' field.
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            )
        })

    # ── Public API ─────────────────────────────────────────────────────────────

    def fetch_prices(self, metals: list[str]) -> dict:
        """
        Main entry point. Tries free web scraping first, then paid APIs,
        then cache, then hardcoded fallback.
        """

        # Tier 1: Free web scraping (Google via Serper + Trading Economics)
        result = self._fetch_from_web(metals)

        # Tier 2: metalpriceapi.com (if key available and Tier 1 missed metals)
        if result is None and METAL_PRICE_API_KEY:
            logger.info("Web scraping returned nothing. Trying metalpriceapi.com...")
            result = self._fetch_metalpriceapi(metals)

        # If Tier 1 got some metals but not all, fill gaps from API or fallback
        if result and METAL_PRICE_API_KEY:
            missing = [m for m in metals if m not in result or not isinstance(result.get(m), (int, float))]
            if missing:
                logger.info(f"Filling missing metals from API: {missing}")
                api_result = self._fetch_metalpriceapi(missing)
                if api_result:
                    for m in missing:
                        if m in api_result and isinstance(api_result[m], (int, float)):
                            result[m] = api_result[m]

        # Tier 3: Cache
        if result is None:
            logger.warning("Live sources failed. Trying cached prices...")
            result = self._load_cache(metals)

        # Tier 4: Hardcoded fallback
        if result is None:
            logger.warning("Cache empty. Falling back to hardcoded estimates.")
            result = self._hardcoded_fallback(metals)

        # Fill any still-missing metals with hardcoded values
        if result:
            for m in metals:
                if m not in result or not isinstance(result.get(m), (int, float)):
                    if m in HARDCODED_FALLBACK:
                        result[m] = HARDCODED_FALLBACK[m]
                        logger.info(f"Filled {m} from hardcoded fallback: ${HARDCODED_FALLBACK[m]}/t")

        # Update cache with any successful fetch
        if result and not result.get("stale", True):
            self._save_cache(result)

        return result

    # ══════════════════════════════════════════════════════════════════════════
    # TIER 1: FREE WEB SCRAPING
    # ══════════════════════════════════════════════════════════════════════════

    def _fetch_from_web(self, metals: list[str]) -> Optional[dict]:
        """
        Tries multiple free web sources to get metal prices.
        Priority: Serper Google Search → Trading Economics guest API
        """
        prices = {}
        sources = []

        # Source A: Google search via Serper API
        serper_prices = self._fetch_via_serper(metals)
        if serper_prices:
            prices.update(serper_prices)
            sources.append("Google Search (Serper)")
            logger.info(f"Serper fetched prices for: {list(serper_prices.keys())}")

        # Source B: Trading Economics guest API (for metals not yet found)
        missing = [m for m in metals if m not in prices]
        if missing:
            te_prices = self._fetch_trading_economics(missing)
            if te_prices:
                prices.update(te_prices)
                sources.append("Trading Economics")
                logger.info(f"Trading Economics fetched prices for: {list(te_prices.keys())}")

        if not prices:
            return None

        return {
            **prices,
            "source": " + ".join(sources),
            "timestamp": datetime.utcnow().isoformat(),
            "stale": False,
        }

    def _fetch_via_serper(self, metals: list[str]) -> dict:
        """
        Uses Serper API (Google Search) to find current metal prices.
        Parses prices from Google's answer boxes and search snippets.
        """
        serper_key = os.environ.get("SERPER_API_KEY", "")
        if not serper_key:
            logger.warning("No SERPER_API_KEY — skipping Google search for prices.")
            return {}

        prices = {}
        headers = {
            "X-API-KEY": serper_key,
            "Content-Type": "application/json",
        }

        for metal in metals:
            try:
                # Search for the current commodity price
                query = f"{metal} price per tonne USD today LME"
                payload = json.dumps({"q": query, "num": 5})

                resp = requests.post(
                    "https://google.serper.dev/search",
                    headers=headers,
                    data=payload,
                    timeout=10,
                )

                if resp.status_code != 200:
                    logger.warning(f"Serper search failed for {metal}: HTTP {resp.status_code}")
                    continue

                data = resp.json()
                price = self._extract_price_from_serper(data, metal)

                if price and price > 0:
                    prices[metal] = price
                    logger.info(f"Serper: {metal} = ${price:.2f}/tonne")

                # Small delay to avoid rate limiting
                time.sleep(0.3)

            except Exception as e:
                logger.warning(f"Serper search error for {metal}: {e}")

        return prices

    def _extract_price_from_serper(self, data: dict, metal: str) -> Optional[float]:
        """
        Extracts a metal price from Serper/Google search results.
        Checks: answerBox → knowledgeGraph → organic snippets
        """
        # 1. Check answer box (Google often shows commodity prices here)
        answer_box = data.get("answerBox", {})
        if answer_box:
            answer_text = answer_box.get("answer", "") or answer_box.get("snippet", "")
            price = self._parse_price_from_text(answer_text, metal)
            if price:
                return price

        # 2. Check knowledge graph
        kg = data.get("knowledgeGraph", {})
        if kg:
            for key in ["price", "description", "attributes"]:
                val = kg.get(key, "")
                if isinstance(val, str):
                    price = self._parse_price_from_text(val, metal)
                    if price:
                        return price
                elif isinstance(val, dict):
                    for v in val.values():
                        price = self._parse_price_from_text(str(v), metal)
                        if price:
                            return price

        # 3. Check organic search result snippets
        for result in data.get("organic", [])[:5]:
            snippet = result.get("snippet", "")
            title = result.get("title", "")
            combined = f"{title} {snippet}"
            price = self._parse_price_from_text(combined, metal)
            if price:
                return price

        return None

    def _parse_price_from_text(self, text: str, metal: str) -> Optional[float]:
        """
        Extracts a USD price from text. Recognizes patterns like:
        - $16,450 per tonne / per metric ton
        - 16,450 USD/t
        - US$16450
        - $16,450.50
        - 16450 USD per tonne
        """
        if not text:
            return None

        # Expected price ranges per metal (USD/tonne) to filter unreasonable values
        PRICE_RANGES = {
            "nickel":    (10000, 35000),
            "cobalt":    (15000, 80000),
            "lithium":   (5000, 90000),
            "manganese": (1000, 8000),
            "lead":      (1500, 4000),
        }

        # Patterns to match prices in text
        patterns = [
            # $16,450.50 or US$16,450 or USD 16,450
            r'(?:US?\$|USD\s*)\s*([\d,]+(?:\.\d{1,2})?)',
            # 16,450 USD or 16450 dollars
            r'([\d,]+(?:\.\d{1,2})?)\s*(?:USD|dollars|usd)',
            # 16,450 per tonne / per metric ton
            r'([\d,]+(?:\.\d{1,2})?)\s*(?:per\s+(?:metric\s+)?tonn?e?|/t\b|/mt\b)',
            # Just large numbers that could be prices (last resort)
            r'\b([\d,]{5,}(?:\.\d{1,2})?)\b',
        ]

        lo, hi = PRICE_RANGES.get(metal, (500, 100000))

        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                try:
                    val = float(match.replace(",", ""))
                    if lo <= val <= hi:
                        return round(val, 2)
                except ValueError:
                    continue

        return None

    def _fetch_trading_economics(self, metals: list[str]) -> dict:
        """
        Trading Economics guest API — free, no key needed.
        Rate-limited but works for basic commodity prices.
        """
        prices = {}

        for metal in metals:
            te_name = TE_COMMODITY_MAP.get(metal)
            if not te_name:
                continue

            try:
                url = f"https://api.tradingeconomics.com/markets/commodity"
                params = {"c": "guest:guest"}
                resp = requests.get(url, params=params, timeout=10)

                if resp.status_code != 200:
                    logger.warning(f"Trading Economics: HTTP {resp.status_code}")
                    continue

                data = resp.json()
                if not isinstance(data, list):
                    continue

                # Search for the metal in the response
                for item in data:
                    name = (item.get("Name", "") or item.get("name", "")).lower()
                    symbol = (item.get("Symbol", "") or item.get("symbol", "")).lower()

                    if te_name in name or te_name in symbol:
                        last_price = item.get("Last") or item.get("last")
                        if last_price:
                            price = float(last_price)
                            # TE returns some metals in USD/tonne, some in other units
                            # Nickel, cobalt, lead are typically per tonne
                            # Lithium carbonate is per tonne
                            # Manganese ore is per DMTU — needs conversion
                            if metal == "manganese" and price < 100:
                                # Manganese ore price in $/DMTU → approx $/tonne
                                price = price * 10  # rough DMTU to tonne factor
                            elif price < 500 and metal != "manganese":
                                # Likely per lb or per kg — convert to per tonne
                                price = price * 1000

                            prices[metal] = round(price, 2)
                            logger.info(f"Trading Economics: {metal} = ${prices[metal]}/tonne")
                            break

                time.sleep(0.5)  # rate limit courtesy

            except Exception as e:
                logger.warning(f"Trading Economics failed for {metal}: {e}")

        return prices

    # ══════════════════════════════════════════════════════════════════════════
    # TIER 2: PAID API (metalpriceapi.com)
    # ══════════════════════════════════════════════════════════════════════════

    def _fetch_metalpriceapi(self, metals: list[str]) -> Optional[dict]:
        """
        metalpriceapi.com — free plan: 100 req/month.
        Fetches each metal individually so one failure doesn't block others.
        """
        TROY_OZ_PER_TONNE = 32150.7
        prices = {}

        for metal in metals:
            if metal == "lithium":
                continue  # Not available on this API

            symbol = LME_SYMBOLS.get(metal)
            if not symbol:
                continue

            try:
                url = "https://api.metalpriceapi.com/v1/latest"
                params = {
                    "api_key": METAL_PRICE_API_KEY,
                    "base": "USD",
                    "currencies": symbol,
                }
                resp = self.session.get(url, params=params, timeout=10)
                resp.raise_for_status()
                data = resp.json()

                if not data.get("success"):
                    err = data.get("error", {})
                    logger.warning(f"metalpriceapi: {metal} ({symbol}) failed: {err}")
                    continue

                rates = data.get("rates", {})
                if symbol in rates:
                    price_per_troy_oz = 1.0 / rates[symbol]
                    prices[metal] = round(price_per_troy_oz * TROY_OZ_PER_TONNE, 2)
                    logger.info(f"metalpriceapi: {metal} = ${prices[metal]}/tonne")

            except Exception as e:
                logger.warning(f"metalpriceapi: {metal} request failed: {e}")

        if not prices:
            return None

        return {
            **prices,
            "source": "metalpriceapi.com",
            "timestamp": datetime.utcnow().isoformat(),
            "stale": False,
        }

    # ══════════════════════════════════════════════════════════════════════════
    # TIER 3 & 4: CACHE + HARDCODED FALLBACK
    # ══════════════════════════════════════════════════════════════════════════

    def _save_cache(self, prices: dict):
        try:
            with open(CACHE_FILE, "w") as f:
                json.dump(prices, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save price cache: {e}")

    def _load_cache(self, metals: list[str]) -> Optional[dict]:
        try:
            if not CACHE_FILE.exists():
                return None
            with open(CACHE_FILE) as f:
                cached = json.load(f)
            cache_time = datetime.fromisoformat(cached.get("timestamp", "2000-01-01"))
            age_hours = (datetime.utcnow() - cache_time).total_seconds() / 3600
            if age_hours > 24:
                logger.warning(f"Cache is {age_hours:.1f}h old — prices may be stale.")
            return {**cached, "stale": age_hours > 8, "source": f"cache ({age_hours:.1f}h old)"}
        except Exception as e:
            logger.error(f"Cache load failed: {e}")
            return None

    def _hardcoded_fallback(self, metals: list[str]) -> dict:
        prices = {m: HARDCODED_FALLBACK.get(m, 0.0) for m in metals}
        return {
            **prices,
            "source": "HARDCODED_FALLBACK — verify manually",
            "timestamp": datetime.utcnow().isoformat(),
            "stale": True,
        }


# Singleton instance
_fetcher = MetalPriceFetcher()


def get_metal_prices(metals: list[str]) -> dict:
    """Public function called by CrewAI tools."""
    return _fetcher.fetch_prices(metals)
