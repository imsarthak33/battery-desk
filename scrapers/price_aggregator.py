"""
scrapers/price_aggregator.py
─────────────────────────────
Runs all scrapers in parallel, aggregates prices into a consensus figure,
and returns a confidence score based on how many sources agree.

Architecture:
  ┌─ InvestingComScraper ─────────────────┐
  ├─ TradingEconomicsScraper ─────────────┤
  ├─ MCXScraper ──────────────────────────┤──► PriceAggregator ──► Consensus Price
  ├─ MetalPriceAPI (paid, if key exists) ─┤
  └─ Cache (fallback) ────────────────────┘

Consensus logic:
  - If ≥3 sources agree within 5% → HIGH confidence
  - If 2 sources agree within 8% → MEDIUM confidence
  - If only 1 source → LOW confidence, flag prominently
  - Outliers (>15% from median) are discarded

All prices normalized to USD/tonne before aggregation.
"""

import json
import logging
import statistics
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

from scrapers.investing_scraper import InvestingComScraper
from scrapers.mcx_scraper import MCXScraper
from scrapers.trading_economics_scraper import TradingEconomicsScraper

logger = logging.getLogger(__name__)

CACHE_FILE = Path(__file__).parent.parent / "cache" / "scraped_prices.json"
CACHE_FILE.parent.mkdir(exist_ok=True)

# Sanity bounds for prices (USD/tonne) — reject anything outside these
PRICE_BOUNDS = {
    "nickel":    (8_000,   80_000),
    "cobalt":    (10_000,  200_000),
    "lithium":   (3_000,   150_000),
    "lead":      (1_000,   10_000),
    "manganese": (500,     10_000),
    "aluminum":  (1_000,   10_000),
}


class PriceAggregator:
    """
    Runs all price scrapers (in parallel threads) and builds a consensus price.
    """

    def __init__(self, usd_inr_rate: float = 84.0, timeout_per_scraper: int = 30):
        self.usd_inr_rate = usd_inr_rate
        self.timeout = timeout_per_scraper

    def fetch_all(self, metals: list[str]) -> dict:
        """
        Main entry point. Returns:
        {
          "nickel": {
              "consensus_price": 16450.0,
              "unit": "USD/tonne",
              "confidence": "HIGH",
              "source_count": 3,
              "sources": [
                {"source": "investing.com", "price": 16440.0},
                {"source": "tradingeconomics.com", "price": 16460.0},
                {"source": "mcxindia.com", "price": 16450.0},
              ],
              "spread_pct": 0.12,
          },
          ...
          "_meta": {...}
        }
        """
        logger.info(f"Running price aggregation for: {metals}")

        # Run all scrapers in parallel
        raw_results = self._run_scrapers_parallel(metals)

        # Build per-metal price lists from all sources
        price_pools = {metal: [] for metal in metals}
        for source_name, source_data in raw_results.items():
            for metal in metals:
                if metal in source_data and isinstance(source_data[metal], dict):
                    price = source_data[metal].get("price")
                    src = source_data[metal].get("source", source_name)
                    if price and self._is_sane(metal, price):
                        price_pools[metal].append({"source": src, "price": price})

        # Compute consensus
        consensus = {}
        for metal in metals:
            pool = price_pools[metal]
            if not pool:
                logger.warning(f"No valid prices scraped for {metal}")
                continue
            consensus[metal] = self._compute_consensus(metal, pool)

        # Cache and return
        result = {
            **consensus,
            "_meta": {
                "timestamp": datetime.utcnow().isoformat(),
                "metals_requested": metals,
                "metals_found": list(consensus.keys()),
                "scrapers_run": list(raw_results.keys()),
            },
        }

        if consensus:  # only cache if we got something
            self._save_cache(result)
        else:
            logger.warning("All scrapers returned empty — loading from cache")
            result = self._load_cache(metals) or result

        return result

    def _run_scrapers_parallel(self, metals: list[str]) -> dict:
        """
        Runs each scraper in a separate thread with a timeout.
        Failed scrapers are logged and skipped.
        """
        scrapers = {
            "investing.com": lambda: InvestingComScraper(metals=metals).scrape(),
            "tradingeconomics.com": lambda: TradingEconomicsScraper(metals=metals).scrape(),
            "mcxindia.com": lambda: MCXScraper(
                metals=[m for m in metals if m in ["nickel", "lead", "aluminum", "zinc"]],
                usd_inr_rate=self.usd_inr_rate,
            ).scrape(),
        }

        # Optionally add API-based fetcher if key exists
        try:
            from tools.price_fetcher import get_metal_prices as api_fetch
            scrapers["metalpriceapi.com"] = lambda: self._wrap_api_fetcher(api_fetch, metals)
        except Exception:
            pass

        results = {}

        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_name = {
                executor.submit(fn): name
                for name, fn in scrapers.items()
            }
            for future in as_completed(future_to_name, timeout=self.timeout + 5):
                name = future_to_name[future]
                try:
                    data = future.result(timeout=self.timeout)
                    if data:
                        results[name] = data
                        metals_found = [k for k in data if not k.startswith("_")]
                        logger.info(f"✓ {name}: found {metals_found}")
                    else:
                        logger.warning(f"✗ {name}: returned empty result")
                except TimeoutError:
                    logger.warning(f"✗ {name}: TIMEOUT after {self.timeout}s")
                except Exception as e:
                    logger.warning(f"✗ {name}: {type(e).__name__}: {e}")

        return results

    def _wrap_api_fetcher(self, api_fetch, metals: list[str]) -> dict:
        """Wraps the API fetcher to match scraper output format."""
        raw = api_fetch(metals)
        wrapped = {}
        for metal in metals:
            if metal in raw and isinstance(raw[metal], (int, float)):
                wrapped[metal] = {
                    "price": float(raw[metal]),
                    "unit": "USD/tonne",
                    "source": raw.get("source", "metalpriceapi.com"),
                }
        return wrapped

    def _is_sane(self, metal: str, price: float) -> bool:
        """Reject prices outside reasonable bounds."""
        bounds = PRICE_BOUNDS.get(metal)
        if not bounds:
            return price > 0
        lo, hi = bounds
        if not (lo <= price <= hi):
            logger.debug(f"Rejected {metal} price {price} — outside bounds [{lo}, {hi}]")
            return False
        return True

    def _compute_consensus(self, metal: str, pool: list[dict]) -> dict:
        """
        Given a pool of {source, price} dicts, compute consensus price and confidence.
        """
        prices = [p["price"] for p in pool]

        if len(prices) == 1:
            return {
                "consensus_price": round(prices[0], 2),
                "unit": "USD/tonne",
                "confidence": "LOW",
                "confidence_reason": "Only 1 source available",
                "source_count": 1,
                "sources": pool,
                "spread_pct": 0.0,
            }

        # Remove outliers (>15% from median)
        med = statistics.median(prices)
        filtered = [p for p in pool if abs(p["price"] - med) / med <= 0.15]
        if not filtered:
            filtered = pool  # if all are outliers, keep all

        filtered_prices = [p["price"] for p in filtered]
        outliers = [p for p in pool if p not in filtered]

        if outliers:
            logger.info(f"  {metal}: discarded {len(outliers)} outlier(s): {[o['price'] for o in outliers]}")

        # Consensus = median of filtered prices (robust to remaining outliers)
        consensus_price = statistics.median(filtered_prices)

        # Spread = (max - min) / median
        spread_pct = 0.0
        if len(filtered_prices) > 1:
            spread_pct = (max(filtered_prices) - min(filtered_prices)) / consensus_price * 100

        # Confidence rating
        n = len(filtered)
        if n >= 3 and spread_pct <= 5.0:
            confidence = "HIGH"
            reason = f"{n} sources agree within {spread_pct:.1f}%"
        elif n >= 2 and spread_pct <= 10.0:
            confidence = "MEDIUM"
            reason = f"{n} sources, spread {spread_pct:.1f}%"
        elif n >= 2:
            confidence = "LOW-MEDIUM"
            reason = f"{n} sources but spread is {spread_pct:.1f}% — significant disagreement"
        else:
            confidence = "LOW"
            reason = "Only 1 valid source after outlier removal"

        return {
            "consensus_price": round(consensus_price, 2),
            "unit": "USD/tonne",
            "confidence": confidence,
            "confidence_reason": reason,
            "source_count": n,
            "sources": filtered,
            "outliers_discarded": outliers,
            "spread_pct": round(spread_pct, 2),
        }

    def _save_cache(self, data: dict):
        try:
            with open(CACHE_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Cache save failed: {e}")

    def _load_cache(self, metals: list[str]) -> Optional[dict]:
        try:
            if not CACHE_FILE.exists():
                return None
            with open(CACHE_FILE) as f:
                cached = json.load(f)
            cache_time = datetime.fromisoformat(cached.get("_meta", {}).get("timestamp", "2000-01-01"))
            age_h = (datetime.utcnow() - cache_time).total_seconds() / 3600
            logger.warning(f"Using cached prices ({age_h:.1f}h old)")

            # Tag everything as stale
            result = {}
            for metal in metals:
                if metal in cached:
                    entry = cached[metal].copy()
                    entry["confidence"] = f"STALE ({age_h:.0f}h old)"
                    result[metal] = entry

            result["_meta"] = {
                **cached.get("_meta", {}),
                "stale": True,
                "cache_age_hours": round(age_h, 1),
            }
            return result
        except Exception as e:
            logger.error(f"Cache load failed: {e}")
            return None


def aggregate_prices(metals: list[str], usd_inr_rate: float = 84.0) -> dict:
    """Public API for the price aggregator."""
    aggregator = PriceAggregator(usd_inr_rate=usd_inr_rate)
    return aggregator.fetch_all(metals)
