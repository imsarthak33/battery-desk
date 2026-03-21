"""
app.py — FastAPI backend v2
Streams agent results live with: article URLs, chart sparkline data, agent thoughts token by token.
"""

import asyncio
import json
import os
import traceback
import random
from datetime import datetime, timedelta
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

app = FastAPI(title="BatteryDesk")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/favicon.ico")
async def favicon():
    return FileResponse("static/favicon.ico")

class AnalysisRequest(BaseModel):
    chemistry: str = "NMC"
    aggregator_ask_inr: float = 300.0


def generate_sparkline(base_price: float, points: int = 30) -> list:
    prices = []
    price = base_price * 0.90
    now = datetime.utcnow()
    for i in range(points):
        price = max(price + price * random.uniform(-0.018, 0.020), base_price * 0.65)
        ts = (now - timedelta(days=points - i)).strftime("%m/%d")
        prices.append({"t": ts, "v": round(price, 2)})
    prices.append({"t": "Live", "v": round(base_price, 2)})
    return prices


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def thought(agent: str, text: str, kind: str = "thinking") -> str:
    return sse("thought", {"agent": agent, "text": text, "kind": kind, "ts": datetime.utcnow().strftime("%H:%M:%S")})


@app.post("/analyze")
async def analyze(req: AnalysisRequest):
    return StreamingResponse(
        pipeline(req.chemistry, req.aggregator_ask_inr),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def pipeline(chemistry: str, ask: float) -> AsyncGenerator[str, None]:
    yield sse("status", {"step": 0, "msg": f"Starting {chemistry} analysis..."})

    try:
        loop = asyncio.get_event_loop()

        # ── 1. FOREX ──
        yield thought("Data Collector", "Connecting to forex APIs for live USD/INR rate...")
        yield sse("status", {"step": 1, "msg": "Fetching live USD/INR..."})

        forex = await loop.run_in_executor(None, lambda: __import__('tools.forex_fetcher', fromlist=['get_usd_inr_rate']).get_usd_inr_rate())
        usd_inr = forex["rate"]

        yield thought("Data Collector", f"✓ USD/INR = ₹{usd_inr:.4f}  |  Source: {forex['source']}", "result")
        yield sse("forex", {"rate": usd_inr, "source": forex["source"]})

        # ── 2. METAL PRICES ──
        from config import BATTERY_CHEMISTRIES
        metals = list(BATTERY_CHEMISTRIES[chemistry]["metals"].keys())

        yield thought("Data Collector", f"Launching parallel scrapers for {', '.join(m.upper() for m in metals)}...")
        yield thought("Data Collector", "Hitting investing.com  →  tradingeconomics.com  →  MCX India  →  metalpriceapi.com")
        yield sse("status", {"step": 2, "msg": "Scraping prices from all sources..."})

        def _prices():
            from scrapers.price_aggregator import aggregate_prices
            return aggregate_prices(metals, usd_inr_rate=usd_inr)

        prices = await loop.run_in_executor(None, _prices)
        price_summary = {}

        for metal, data in prices.items():
            if metal.startswith("_"):
                continue
            price_summary[metal] = data
            cp = data.get("consensus_price", 0)
            conf = data.get("confidence", "LOW")
            srcs = [s.get("source", "") for s in data.get("sources", [])[:3]]
            yield thought("Data Collector", f"  {metal.upper()}: ${cp:,.0f}/t  |  {conf}  |  {', '.join(srcs) or 'fallback'}", "result")
            yield sse("metal_price", {
                "metal": metal, "price": cp, "confidence": conf,
                "sources": data.get("source_count", 1),
                "spread": data.get("spread_pct", 0),
                "source_list": data.get("sources", [])[:4],
                "chart": generate_sparkline(cp),
            })
            await asyncio.sleep(0.04)

        # ── 3. CALCULATION ──
        yield thought("Margin Calculator", f"Price data received. Running Python margin stack for {chemistry}...")
        yield thought("Margin Calculator", f"Ask: ₹{ask}/kg  |  USD/INR: ₹{usd_inr:.2f}  |  Payable rate: 75%")
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
        yield thought("Margin Calculator", f"Gross metal value: ${a.gross_metal_value_usd:.4f}/kg  →  Selling price INR: ₹{a.selling_price_inr:.2f}", "result")
        yield thought("Margin Calculator", f"After deductions → MAX BUY PRICE: ₹{a.max_buy_price_inr:.2f}/kg", "result")
        yield thought("Margin Calculator", f"Decision: Ask ₹{ask} vs Max ₹{a.max_buy_price_inr:.2f} → {'✅ BUY' if a.buy_decision else '🛑 HARD STOP'}", "decision")

        yield sse("calculation", {
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
        })

        # ── 4. NEWS ──
        yield thought("Intelligence Analyst", f"Launching news scrapers for {', '.join(metals)}...")
        yield thought("Intelligence Analyst", "Reuters  →  Mining.com  →  Economic Times  →  LME Notices")
        yield sse("status", {"step": 4, "msg": "Scraping Reuters, Mining.com, ET, LME..."})

        def _news():
            from scrapers.news_aggregator import get_market_intelligence
            return get_market_intelligence(metals)

        news = await loop.run_in_executor(None, _news)
        sentiment = news.get("sentiment_summary", {})

        yield thought("Intelligence Analyst", f"Scraped {news.get('articles_total', 0)} articles. Computing sentiment signals...", "result")
        for metal, s in sentiment.items():
            if s.get("total_articles", 0) > 0:
                yield thought("Intelligence Analyst",
                    f"  {metal.upper()}: {s['signal']}  |  ↑{s['bullish']} bullish  ↓{s['bearish']} bearish  →{s['neutral']} neutral",
                    "result")

        enriched = [{
            "metal": a.get("metal", ""), "headline": a.get("headline", ""),
            "source": a.get("source", ""), "url": a.get("url", ""),
            "sentiment": a.get("sentiment", "neutral"),
            "price_mentions": a.get("price_mentions", []),
        } for a in news.get("articles", [])[:20]]

        yield sse("news", {
            "sentiment": sentiment,
            "articles": enriched,
            "headlines": news.get("top_headlines", [])[:10],
            "price_mentions": news.get("price_mentions", [])[:8],
            "lme_inventory": news.get("lme_inventory", {}),
            "articles_total": news.get("articles_total", 0),
        })

        # ── 5. CREW FORECAST ──
        yield thought("Forecaster", "Synthesizing all signals for final price forecast...")
        yield thought("Forecaster", f"Inputs: {len(enriched)} articles, {len(news.get('price_mentions',[]))} price mentions, LME inventory, sentiment model")
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

        yield thought("Forecaster", f"Final recommendation: {signal}", "decision")
        yield sse("forecast", {"signal": signal, "full_report": crew_result})
        yield sse("done", {"ts": datetime.utcnow().isoformat()})

    except Exception as e:
        yield thought("System", f"Pipeline error: {e}", "error")
        yield sse("error", {"message": str(e), "detail": traceback.format_exc()})


@app.get("/health")
def health(): return {"status": "ok"}

@app.get("/chemistries")
def chemistries():
    from config import BATTERY_CHEMISTRIES
    return {k: {"full_name": v["full_name"], "metals": list(v["metals"].keys()), "notes": v["notes"]} for k, v in BATTERY_CHEMISTRIES.items()}

@app.get("/")
def root():
    with open("static/index.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())
