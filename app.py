"""
app.py — FastAPI backend
Streams agent results live to the frontend using Server-Sent Events.
"""

import asyncio
import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

BASE_DIR = Path(__file__).parent

app = FastAPI(title="Battery Scrap Analyzer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request model ──────────────────────────────────────────────────────────────

class AnalysisRequest(BaseModel):
    chemistry: str = "NMC"
    aggregator_ask_inr: float = 300.0


# ── SSE streaming endpoint ─────────────────────────────────────────────────────

@app.post("/analyze")
async def analyze(req: AnalysisRequest):
    """
    Runs the full agent pipeline and streams progress events to the frontend.
    Uses Server-Sent Events so the UI updates in real-time.
    """
    return StreamingResponse(
        run_pipeline_stream(req.chemistry, req.aggregator_ask_inr),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def run_pipeline_stream(chemistry: str, ask: float) -> AsyncGenerator[str, None]:
    """Runs agents and yields SSE events as each step completes."""

    def send(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    yield send("status", {"message": f"🚀 Starting analysis for {chemistry}...", "step": 0})
    await asyncio.sleep(0.1)

    try:
        # ── Step 1: Forex ──────────────────────────────────────────────────────
        yield send("status", {"message": "💱 Fetching live USD/INR rate...", "step": 1})
        await asyncio.sleep(0.1)

        loop = asyncio.get_event_loop()

        def fetch_forex():
            from tools.forex_fetcher import get_usd_inr_rate
            return get_usd_inr_rate()

        forex = await loop.run_in_executor(None, fetch_forex)
        usd_inr = forex["rate"]

        yield send("forex", {
            "rate": usd_inr,
            "source": forex["source"],
            "timestamp": forex["timestamp"],
        })

        # ── Step 2: Metal Prices ───────────────────────────────────────────────
        yield send("status", {"message": "⛏️  Fetching live metal prices from Google & free sources...", "step": 2})
        await asyncio.sleep(0.1)

        from config import BATTERY_CHEMISTRIES
        metals = list(BATTERY_CHEMISTRIES[chemistry]["metals"].keys())

        def fetch_prices():
            from tools.price_fetcher import get_metal_prices
            return get_metal_prices(metals)

        raw_prices = await loop.run_in_executor(None, fetch_prices)

        price_source = raw_prices.pop("source", "Google Search")
        price_stale = raw_prices.pop("stale", False)
        raw_prices.pop("timestamp", None)

        # Build metal_prices dict and send individual metal events to frontend
        metal_prices_usd = {}
        for metal in metals:
            price_val = raw_prices.get(metal)
            if price_val and isinstance(price_val, (int, float)) and price_val > 0:
                metal_prices_usd[metal] = float(price_val)
                yield send("metal_price", {
                    "metal": metal,
                    "price": float(price_val),
                    "confidence": "HIGH-Live",
                    "sources": 1,
                    "spread": 0.0,
                })
            else:
                yield send("metal_price", {
                    "metal": metal,
                    "price": 0,
                    "confidence": "UNKNOWN",
                    "sources": 0,
                    "spread": 0.0,
                })
            await asyncio.sleep(0.05)

        # ── Step 3: Margin Calculation ─────────────────────────────────────────
        yield send("status", {"message": "📊 Calculating margin stack...", "step": 3})
        await asyncio.sleep(0.1)

        def calculate():
            from tools.calculator import calculate_max_buy_price, format_analysis_report
            analysis = calculate_max_buy_price(
                chemistry_key=chemistry,
                metal_prices_usd_per_tonne=metal_prices_usd,
                usd_inr_rate=usd_inr,
                aggregator_ask_inr=ask,
                price_source=price_source,
                forex_source=forex["source"],
                stale_data=price_stale,
            )
            return analysis

        analysis = await loop.run_in_executor(None, calculate)

        yield send("calculation", {
            "chemistry": chemistry,
            "full_name": analysis.full_name,
            "gross_metal_value_usd": analysis.gross_metal_value_usd,
            "selling_price_usd": analysis.selling_price_usd,
            "selling_price_inr": analysis.selling_price_inr,
            "max_buy_price_inr": analysis.max_buy_price_inr,
            "aggregator_ask_inr": analysis.aggregator_ask_inr,
            "buy_decision": analysis.buy_decision,
            "margin_at_ask_pct": analysis.margin_at_ask_pct,
            "metal_breakdown": analysis.metal_breakdown_usd,
            "warnings": analysis.warnings,
            "usd_inr_rate": usd_inr,
        })

        # ── Step 4: Market Intelligence ────────────────────────────────────────
        yield send("status", {"message": "📰 Fetching market news...", "step": 4})
        await asyncio.sleep(0.1)

        try:
            def fetch_news():
                from scrapers.news_aggregator import get_market_intelligence
                return get_market_intelligence(metals)

            news = await loop.run_in_executor(None, fetch_news)
        except Exception as news_err:
            # News is optional — don't crash the pipeline
            news = {"sentiment_summary": {}, "top_headlines": [], "price_mentions": [], "articles_total": 0}

        sentiment = news.get("sentiment_summary", {})
        headlines = news.get("top_headlines", [])
        price_mentions = news.get("price_mentions", [])

        yield send("news", {
            "sentiment": sentiment,
            "headlines": headlines[:8],
            "price_mentions": price_mentions[:6],
            "lme_inventory": news.get("lme_inventory", {}),
            "articles_total": news.get("articles_total", 0),
        })

        # ── Step 5: Run CrewAI agents for forecast ─────────────────────────────
        yield send("status", {"message": "🤖 Running AI agents for price forecast...", "step": 5})
        await asyncio.sleep(0.1)

        def run_crew():
            from crewai import Crew, Process
            from agents_and_tasks_v2 import (
                make_tasks_v2,
                data_fetcher_agent,
                margin_calculator_agent,
                market_forecaster_agent,
            )
            tasks = make_tasks_v2(chemistry, ask)
            crew = Crew(
                agents=[data_fetcher_agent, margin_calculator_agent, market_forecaster_agent],
                tasks=tasks,
                process=Process.sequential,
                verbose=False,
                max_rpm=8,
            )
            return str(crew.kickoff())

        crew_result = await loop.run_in_executor(None, run_crew)

        # Parse SELL/HOLD signal from crew output
        forecast_signal = "HOLD INVENTORY"
        if "SELL TODAY" in crew_result.upper():
            forecast_signal = "SELL TODAY"
        elif "HOLD" in crew_result.upper():
            forecast_signal = "HOLD INVENTORY"

        yield send("forecast", {
            "signal": forecast_signal,
            "full_report": crew_result,
        })

        # ── Done ───────────────────────────────────────────────────────────────
        yield send("done", {
            "message": "Analysis complete",
            "timestamp": datetime.utcnow().isoformat(),
        })

    except Exception as e:
        yield send("error", {
            "message": str(e),
            "detail": traceback.format_exc(),
        })


# ── Health check ───────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.get("/chemistries")
def get_chemistries():
    from config import BATTERY_CHEMISTRIES
    return {
        k: {"full_name": v["full_name"], "metals": list(v["metals"].keys()), "notes": v["notes"]}
        for k, v in BATTERY_CHEMISTRIES.items()
    }


# ── Serve frontend ─────────────────────────────────────────────────────────────

@app.get("/")
def root():
    index_path = BASE_DIR / "static" / "index.html"
    with open(index_path, encoding="utf-8") as f:
        return HTMLResponse(f.read())


# Mount static files AFTER routes so / doesn't get intercepted
static_dir = BASE_DIR / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
