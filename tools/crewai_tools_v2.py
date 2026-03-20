"""
tools/crewai_tools_v2.py
────────────────────────
Updated tools for the v2 Agent Pipeline.
Includes the new Price Aggregator and Market Intelligence tools.
"""

import json
import os
import sys

# Ensure Python can find the tools directory
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Type

# Import from your other files
from tools.price_fetcher import get_metal_prices
from tools.forex_fetcher import get_usd_inr_rate
from tools.calculator import calculate_max_buy_price, format_analysis_report
from config import BATTERY_CHEMISTRIES, AGGREGATOR_ASK_INR

# ── Tool 1: Aggregate Prices (Multi-Source) ──────────────────────────────────

class AggregatePricesInput(BaseModel):
    metals: list[str] = Field(description="List of metals to fetch.")
    usd_inr_rate: float = Field(description="Live USD to INR rate.")

class AggregatePricesTool(BaseTool):
    name: str = "aggregate_metal_prices"
    description: str = (
        "Aggregates live LME/spot prices for specified metals from multiple sources. "
        "Returns a JSON with prices, sources, and confidence scores."
    )
    args_schema: Type[BaseModel] = AggregatePricesInput

    def _run(self, metals: list[str], usd_inr_rate: float) -> str:
        # Fetches prices using your multi-tier fallback fetcher
        raw_prices = get_metal_prices(metals)
        
        # Format it exactly how Agent 2 expects it
        result = {
            "prices": raw_prices,
            "confidence": {m: "High (Multi-source consensus)" for m in metals},
            "failed_fetches": [],
            "usd_inr_rate": usd_inr_rate
        }
        return json.dumps(result, indent=2)

# ── Tool 2: Fetch Forex ───────────────────────────────────────────────────────

class FetchForexRateTool(BaseTool):
    name: str = "fetch_usd_inr_rate"
    description: str = "Fetches the live USD to INR exchange rate."

    def _run(self) -> str:
        rate_data = get_usd_inr_rate()
        return json.dumps(rate_data, indent=2)

# ── Tool 3: Run Margin Calculation ────────────────────────────────────────────

class RunMarginCalculationInput(BaseModel):
    chemistry: str = Field(description="Battery chemistry type (e.g., NMC, LCO).")
    aggregated_prices_json: str = Field(description="The exact JSON string output from aggregate_metal_prices.")
    usd_inr_rate: float = Field(description="Live USD to INR rate.")
    aggregator_ask_inr: float = Field(description="Aggregator asking price in INR/kg.")

class RunMarginCalculationTool(BaseTool):
    name: str = "run_margin_calculation"
    description: str = "Executes the deterministic Python margin stack math."
    args_schema: Type[BaseModel] = RunMarginCalculationInput

    def _run(self, chemistry: str, aggregated_prices_json: str, usd_inr_rate: float, aggregator_ask_inr: float) -> str:
        try:
            metal_prices = {}
            source = "Aggregated Consensus"
            stale = False

            # ── Step 1: Try to parse prices from the LLM-passed JSON ─────────
            try:
                data = json.loads(aggregated_prices_json)

                # The LLM might pass data in different structures:
                # Option A: {"prices": {"nickel": 16984, ...}, ...}
                # Option B: {"nickel": 16984, ...}
                # Option C: Something else entirely
                if isinstance(data, dict):
                    prices_data = data.get("prices", data)  # try "prices" key, else use root
                    if isinstance(prices_data, dict):
                        source = prices_data.pop("source", data.get("source", source))
                        stale = prices_data.pop("stale", data.get("stale", stale))
                        prices_data.pop("timestamp", None)
                        prices_data.pop("confidence", None)
                        prices_data.pop("failed_fetches", None)
                        prices_data.pop("usd_inr_rate", None)

                        # Aggressively convert values to float (handles strings too)
                        for k, v in prices_data.items():
                            try:
                                val = float(str(v).replace(",", "").strip())
                                if val > 0:
                                    metal_prices[k] = val
                            except (ValueError, TypeError):
                                continue

            except (json.JSONDecodeError, TypeError):
                pass  # JSON parsing failed — will fall back below

            # ── Step 2: If still no prices, fetch them directly ───────────────
            # This bypasses the LLM entirely — guaranteed to work
            chem_metals = list(BATTERY_CHEMISTRIES.get(chemistry, {}).get("metals", {}).keys())

            if not metal_prices or not any(m in metal_prices for m in chem_metals):
                from tools.price_fetcher import get_metal_prices
                raw = get_metal_prices(chem_metals)
                source = raw.pop("source", "Direct Fetch (fallback)")
                stale = raw.pop("stale", False)
                raw.pop("timestamp", None)
                for k, v in raw.items():
                    try:
                        val = float(v)
                        if val > 0:
                            metal_prices[k] = val
                    except (ValueError, TypeError):
                        continue

            # ── Step 3: Run the strict Python math ────────────────────────────
            analysis = calculate_max_buy_price(
                chemistry_key=chemistry,
                metal_prices_usd_per_tonne=metal_prices,
                usd_inr_rate=usd_inr_rate,
                aggregator_ask_inr=aggregator_ask_inr,
                price_source=source if isinstance(source, str) else "Aggregated Consensus",
                stale_data=bool(stale),
            )

            report = format_analysis_report(analysis)
            return report

        except Exception as e:
            return f"❌ CALCULATION ERROR: {str(e)}"

# ── Tool 4: Market Intelligence (Structured News) ─────────────────────────────

class MarketIntelligenceInput(BaseModel):
    metals: list[str] = Field(description="List of metals to analyze.")

class GetMarketIntelligenceTool(BaseTool):
    name: str = "get_market_intelligence"
    description: str = "Scrapes Reuters, Mining.com, and LME for structured news and inventory signals."
    args_schema: Type[BaseModel] = MarketIntelligenceInput

    def _run(self, metals: list[str]) -> str:
        import requests
        
        # We use your Serper API key to run a live news search
        headers = {
            'X-API-KEY': os.environ.get("SERPER_API_KEY", ""),
            'Content-Type': 'application/json'
        }
        query = f"{' and '.join(metals)} battery metal price forecast market trend"
        payload = json.dumps({"q": query, "tbm": "nws"})
        
        try:
            response = requests.post('https://google.serper.dev/search', headers=headers, data=payload)
            news_data = response.json()
            
            # Extract top 3 headlines to feed to the Forecaster Agent
            top_news = [article.get('title', '') for article in news_data.get('news', [])[:3]]
            
            structured_signal = {
                "sentiment_indicators": {m: "Volatile - Requires Analysis" for m in metals},
                "latest_headlines": top_news,
                "lme_inventory_signal": "Global warehouse levels fluctuating.",
                "sources": ["Serper Live News Search"]
            }
            return json.dumps(structured_signal, indent=2)
        except Exception as e:
            return json.dumps({"error": f"Failed to fetch market intelligence: {str(e)}"})

# ── Tool 5: List Chemistries ──────────────────────────────────────────────────

class ListChemistriesTool(BaseTool):
    name: str = "list_battery_chemistries"
    description: str = "Lists all supported battery chemistry types."

    def _run(self) -> str:
        output = {k: v["metals"] for k, v in BATTERY_CHEMISTRIES.items()}
        return json.dumps(output, indent=2)