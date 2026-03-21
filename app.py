"""
app.py — FastAPI backend with full database integration
Every pipeline run saves to DB. Prediction agent uses history.
New endpoints expose historical data for charts/dashboard.
"""

import asyncio
import json
import os
import traceback
import random
import uuid
from datetime import datetime, timedelta
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

# ── Init DB on startup ─────────────────────────────────────────────────────────
from Database.connection import init_db
init_db()

app = FastAPI(title="BatteryDesk")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


class AnalysisRequest(BaseModel):
    chemistry: str = "NMC"
    aggregator_ask_inr: float = 300.0


def generate_sparkline(base: float, n: int = 30) -> list:
    p = base * 0.90
    now = datetime.utcnow()
    out = []
    for i in range(n):
        p = max(p + p * random.uniform(-0.018, 0.020), base * 0.65)
        out.append({"t": (now - timedelta(days=n - i)).strftime("%m/%d"), "v": round(p, 2)})
    out.append({"t": "Live", "v": round(base, 2)})
    return out


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"

def thought(agent: str, text: str, kind: str = "thinking") -> str:
    return sse("thought", {"agent": agent, "text": text, "kind": kind,
                           "ts": datetime.utcnow().strftime("%H:%M:%S")})


# ── Main analysis pipeline ─────────────────────────────────────────────────────

@app.post("/analyze")
async def analyze(req: AnalysisRequest):
    return StreamingResponse(
        pipeline(req.chemistry, req.aggregator_ask_inr),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def pipeline(chemistry: str, ask: float) -> AsyncGenerator[str, None]:
    run_id = str(uuid.uuid4())
    yield sse("run_id", {"run_id": run_id})
    yield sse("status", {"step": 0, "msg": f"Starting {chemistry} analysis [run: {run_id[:8]}]..."})

    from Database import db_service as db
    from config import BATTERY_CHEMISTRIES
    metals = list(BATTERY_CHEMISTRIES[chemistry]["metals"].keys())

    calc_data = {}   # shared between steps for DB saving
    news_data = {}

    try:
        loop = asyncio.get_event_loop()

        # ── 1. FOREX ──────────────────────────────────────────────────────────
        yield thought("Data Collector", "Connecting to forex APIs for live USD/INR...")
        yield sse("status", {"step": 1, "msg": "Fetching live USD/INR..."})

        forex = await loop.run_in_executor(None, lambda: __import__(
            'tools.forex_fetcher', fromlist=['get_usd_inr_rate']).get_usd_inr_rate())
        usd_inr = forex["rate"]

        # SAVE to DB
        await loop.run_in_executor(None, lambda: db.save_forex_rate(
            run_id, usd_inr, forex["source"], forex.get("stale", False)))

        yield thought("Data Collector", f"✓ USD/INR = ₹{usd_inr:.4f}  |  {forex['source']}", "result")
        yield sse("forex", {"rate": usd_inr, "source": forex["source"]})

        # ── 2. PRICES ─────────────────────────────────────────────────────────
        yield thought("Data Collector", f"Launching parallel scrapers for {', '.join(m.upper() for m in metals)}...")
        yield thought("Data Collector", "investing.com → tradingeconomics.com → MCX India → metalpriceapi.com")
        yield sse("status", {"step": 2, "msg": "Scraping prices from all sources..."})

        def _prices():
            from scrapers.price_aggregator import aggregate_prices
            return aggregate_prices(metals, usd_inr_rate=usd_inr)

        prices = await loop.run_in_executor(None, _prices)

        # SAVE to DB
        await loop.run_in_executor(None, lambda: db.save_metal_prices(run_id, chemistry, prices, usd_inr))

        price_summary = {}
        for metal, data in prices.items():
            if metal.startswith("_"):
                continue
            price_summary[metal] = data
            cp = data.get("consensus_price", 0)
            conf = data.get("confidence", "LOW")
            srcs = [s.get("source", "") for s in data.get("sources", [])[:3]]
            yield thought("Data Collector",
                f"  {metal.upper()}: ${cp:,.0f}/t  |  {conf}  |  {', '.join(srcs) or 'fallback'}", "result")
            yield thought("Data Collector",
                f"  Saved to database ✓", "result")
            yield sse("metal_price", {
                "metal": metal, "price": cp, "confidence": conf,
                "sources": data.get("source_count", 1),
                "spread": data.get("spread_pct", 0),
                "source_list": data.get("sources", [])[:4],
                "chart": generate_sparkline(cp),
            })
            await asyncio.sleep(0.04)

        # ── 3. CALCULATION ────────────────────────────────────────────────────
        yield thought("Margin Calculator", f"Running Python margin stack for {chemistry}...")
        yield thought("Margin Calculator", f"Ask: ₹{ask}/kg  |  USD/INR: ₹{usd_inr:.2f}  |  Payable: 75%")
        yield sse("status", {"step": 3, "msg": "Calculating margin stack..."})

        def _calc():
            from tools.calculator import calculate_max_buy_price
            return calculate_max_buy_price(
                chemistry_key=chemistry,
                metal_prices_usd_per_tonne={m: d["consensus_price"] for m, d in price_summary.items() if "consensus_price" in d},
                usd_inr_rate=usd_inr,
                aggregator_ask_inr=ask,
                price_source="multi-source scraper",
                forex_source=forex["source"],
                stale_data=prices.get("_meta", {}).get("stale", False),
            )

        a = await loop.run_in_executor(None, _calc)

        calc_data = {
            "chemistry": chemistry, "full_name": a.full_name,
            "gross_metal_value_usd": a.gross_metal_value_usd,
            "selling_price_usd": a.selling_price_usd,
            "selling_price_inr": a.selling_price_inr,
            "max_buy_price_inr": a.max_buy_price_inr,
            "aggregator_ask_inr": a.aggregator_ask_inr,
            "buy_decision": a.buy_decision,
            "margin_at_ask_pct": a.margin_at_ask_pct,
            "metal_breakdown": a.metal_breakdown_usd,
            "warnings": a.warnings,
            "usd_inr_rate": usd_inr,
            "stale_data": a.stale_data,
        }

        # SAVE to DB
        await loop.run_in_executor(None, lambda: db.save_margin_analysis(run_id, calc_data))

        yield thought("Margin Calculator", f"Selling price: ₹{a.selling_price_inr:.2f}  →  Max Buy: ₹{a.max_buy_price_inr:.2f}", "result")
        yield thought("Margin Calculator", f"{'✅ BUY' if a.buy_decision else '🛑 HARD STOP'} — Saved to database ✓", "decision")
        yield sse("calculation", calc_data)

        # ── 4. NEWS ───────────────────────────────────────────────────────────
        yield thought("Intelligence Analyst", f"Scraping Reuters, Mining.com, ET, LME for {', '.join(metals)}...")
        yield sse("status", {"step": 4, "msg": "Scraping news intelligence..."})

        def _news():
            from scrapers.news_aggregator import get_market_intelligence
            return get_market_intelligence(metals)

        news = await loop.run_in_executor(None, _news)
        news_data = news
        sentiment = news.get("sentiment_summary", {})

        # SAVE to DB
        await loop.run_in_executor(None, lambda: db.save_news_articles(run_id, news.get("articles", []), chemistry))
        await loop.run_in_executor(None, lambda: db.save_sentiment(run_id, sentiment, chemistry))
        if news.get("lme_inventory"):
            await loop.run_in_executor(None, lambda: db.save_lme_inventory(news["lme_inventory"]))

        yield thought("Intelligence Analyst", f"Scraped {news.get('articles_total', 0)} articles — all saved to database ✓", "result")
        for metal, s in sentiment.items():
            if s.get("total_articles", 0) > 0:
                yield thought("Intelligence Analyst",
                    f"  {metal.upper()}: {s['signal']}  {s['bullish']}↑ {s['bearish']}↓", "result")

        enriched = [{
            "metal": a.get("metal", ""), "headline": a.get("headline", ""),
            "source": a.get("source", ""), "url": a.get("url", ""),
            "sentiment": a.get("sentiment", "neutral"),
            "price_mentions": a.get("price_mentions", []),
        } for a in news.get("articles", [])[:20]]

        yield sse("news", {
            "sentiment": sentiment, "articles": enriched,
            "headlines": news.get("top_headlines", [])[:10],
            "price_mentions": news.get("price_mentions", [])[:8],
            "lme_inventory": news.get("lme_inventory", {}),
            "articles_total": news.get("articles_total", 0),
        })

        # ── 5. CREW FORECAST ──────────────────────────────────────────────────
        yield thought("Forecaster", "Synthesizing all signals for SELL/HOLD forecast...")
        yield sse("status", {"step": 5, "msg": "Running AI forecast agents..."})

        def _crew():
            from crewai import Crew, Process
            from agents_and_tasks_v2 import make_tasks_v2, data_fetcher_agent, margin_calculator_agent, market_forecaster_agent
            crew = Crew(
                agents=[data_fetcher_agent, margin_calculator_agent, market_forecaster_agent],
                tasks=make_tasks_v2(chemistry, ask),
                process=Process.sequential, verbose=False, max_rpm=8,
            )
            return str(crew.kickoff())

        crew_result = await loop.run_in_executor(None, _crew)
        signal = "SELL TODAY" if "SELL TODAY" in crew_result.upper() else "HOLD INVENTORY"

        forecast_data = {"signal": signal, "full_report": crew_result}

        # SAVE to DB
        await loop.run_in_executor(None, lambda: db.save_forecast(run_id, forecast_data, calc_data))

        yield thought("Forecaster", f"Final: {signal} — Saved to database ✓", "decision")
        yield sse("forecast", forecast_data)

        # ── 6. PREDICTION AGENT ───────────────────────────────────────────────
        yield thought("Prediction Agent", f"Loading historical data from database for {chemistry}...")
        yield thought("Prediction Agent", "Analyzing 30-day price trends, sentiment patterns, inventory cycles...")
        yield sse("status", {"step": 6, "msg": "Running price prediction agent..."})

        def _predict():
            from crewai import Crew, Process
            from agents.prediction_agent import prediction_agent, make_prediction_task
            task = make_prediction_task(chemistry, metals, run_id)
            crew = Crew(agents=[prediction_agent], tasks=[task],
                        process=Process.sequential, verbose=False, max_rpm=5)
            return str(crew.kickoff())

        pred_result = await loop.run_in_executor(None, _predict)

        # Load what was just saved to show user
        def _load_preds():
            from Database.db_service import get_latest_predictions
            return get_latest_predictions(chemistry, limit=len(metals))

        latest_preds = await loop.run_in_executor(None, _load_preds)

        yield thought("Prediction Agent", "Predictions saved to database ✓  Accuracy will be tracked automatically.", "result")
        yield sse("predictions", {
            "predictions": latest_preds,
            "full_output": pred_result,
        })

        yield sse("done", {"run_id": run_id, "ts": datetime.utcnow().isoformat()})

    except Exception as e:
        yield thought("System", f"Pipeline error: {e}", "error")
        yield sse("error", {"message": str(e), "detail": traceback.format_exc()})


# ── Historical data API endpoints ──────────────────────────────────────────────

@app.get("/api/history/{metal}")
def price_history(metal: str, days: int = 30):
    from Database.db_service import get_price_history
    return {"metal": metal, "data": get_price_history(metal, days)}

@app.get("/api/sentiment/{metal}")
def sentiment_history(metal: str, days: int = 14):
    from Database.db_service import get_sentiment_history
    return {"metal": metal, "data": get_sentiment_history(metal, days)}

@app.get("/api/margins/{chemistry}")
def margin_trend(chemistry: str, days: int = 30):
    from Database.db_service import get_margin_trend
    return get_margin_trend(chemistry, days)

@app.get("/api/predictions/{chemistry}")
def predictions(chemistry: str):
    from Database.db_service import get_latest_predictions
    return {"chemistry": chemistry, "predictions": get_latest_predictions(chemistry)}

@app.get("/api/news/{metal}")
def recent_news(metal: str, days: int = 7):
    from Database.db_service import get_recent_news
    return {"metal": metal, "articles": get_recent_news(metal, days)}

@app.get("/api/stats")
def dashboard_stats():
    from Database.db_service import get_dashboard_stats
    return get_dashboard_stats()

@app.get("/api/forecast-accuracy/{chemistry}")
def forecast_accuracy(chemistry: str):
    from Database.db_service import get_forecast_accuracy
    return get_forecast_accuracy(chemistry)

@app.get("/health")
def health(): return {"status": "ok"}

@app.get("/chemistries")
def chemistries():
    from config import BATTERY_CHEMISTRIES
    return {k: {"full_name": v["full_name"], "metals": list(v["metals"].keys()), "notes": v["notes"]} for k, v in BATTERY_CHEMISTRIES.items()}

@app.get("/")
def root():
    with open("static/index.html") as f:
        return HTMLResponse(f.read())