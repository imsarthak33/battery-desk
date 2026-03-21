"""
database/db_service.py
───────────────────────
All database read/write operations.
Called by app.py during each pipeline run and by the Prediction Agent.
"""

import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, and_

from database.models import (
    MetalPrice, ForexRate, MarginAnalysis, NewsArticle,
    SentimentScore, LMEInventory, Forecast, PricePrediction,
)
from database.connection import get_db

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  WRITE OPERATIONS — called during each pipeline run
# ══════════════════════════════════════════════════════════════════════════════

def save_forex_rate(run_id: str, rate: float, source: str, stale: bool = False):
    with get_db() as db:
        db.add(ForexRate(rate=rate, source=source, stale=stale))
    logger.debug(f"Saved forex rate: ₹{rate}")


def save_metal_prices(run_id: str, chemistry: str, prices_data: dict, usd_inr: float):
    """Save all metal prices from one pipeline run."""
    with get_db() as db:
        for metal, data in prices_data.items():
            if metal.startswith("_") or not isinstance(data, dict):
                continue
            cp = data.get("consensus_price", 0)
            row = MetalPrice(
                run_id=run_id,
                metal=metal,
                chemistry=chemistry,
                consensus_price_usd=cp,
                source_count=data.get("source_count", 1),
                spread_pct=data.get("spread_pct", 0.0),
                confidence=data.get("confidence", "LOW"),
                source_prices={s.get("source", ""): s.get("price", 0) for s in data.get("sources", [])},
                price_inr_per_kg=round((cp / 1000) * usd_inr, 4) if cp and usd_inr else None,
                usd_inr_rate=usd_inr,
                chart_data=data.get("chart", []),
            )
            db.add(row)
    logger.debug(f"Saved metal prices for run {run_id[:8]}")


def save_margin_analysis(run_id: str, analysis_data: dict):
    """Save the margin calculation result."""
    with get_db() as db:
        row = MarginAnalysis(
            run_id=run_id,
            chemistry=analysis_data["chemistry"],
            aggregator_ask_inr=analysis_data["aggregator_ask_inr"],
            usd_inr_rate=analysis_data["usd_inr_rate"],
            metal_prices_json=analysis_data.get("metal_breakdown", {}),
            gross_metal_value_usd=analysis_data["gross_metal_value_usd"],
            selling_price_usd=analysis_data["selling_price_usd"],
            selling_price_inr=analysis_data["selling_price_inr"],
            max_buy_price_inr=analysis_data["max_buy_price_inr"],
            buy_decision=analysis_data["buy_decision"],
            margin_at_ask_pct=analysis_data.get("margin_at_ask_pct"),
            headroom_inr=analysis_data["max_buy_price_inr"] - analysis_data["aggregator_ask_inr"],
            stale_data=analysis_data.get("stale_data", False),
            warnings=analysis_data.get("warnings", []),
        )
        db.add(row)
    logger.debug(f"Saved margin analysis for {analysis_data['chemistry']}")


def save_news_articles(run_id: str, articles: list, chemistry: str = ""):
    """Save scraped articles, skipping duplicates by headline hash."""
    with get_db() as db:
        saved = 0
        for a in articles:
            headline = a.get("headline", "").strip()
            if not headline:
                continue
            h_hash = hashlib.md5(headline.lower().encode()).hexdigest()

            # Check duplicate
            existing = db.query(NewsArticle).filter_by(headline_hash=h_hash).first()
            if existing:
                continue

            row = NewsArticle(
                run_id=run_id,
                metal=a.get("metal", "unknown"),
                chemistry=chemistry,
                source=a.get("source", ""),
                headline=headline,
                url=a.get("url", ""),
                sentiment=a.get("sentiment", "neutral"),
                price_mentions=a.get("price_mentions", []),
                headline_hash=h_hash,
            )
            db.add(row)
            saved += 1
    logger.debug(f"Saved {saved} new articles (deduped)")


def save_sentiment(run_id: str, sentiment_data: dict, chemistry: str = ""):
    """Upsert daily sentiment scores."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    with get_db() as db:
        for metal, s in sentiment_data.items():
            total = s.get("total_articles", 0)
            if total == 0:
                continue
            bull = s.get("bullish", 0)
            bear = s.get("bearish", 0)
            ratio = round(bull / total, 4) if total > 0 else 0.0

            # Upsert: update if exists for same date+metal, else insert
            existing = db.query(SentimentScore).filter_by(date=today, metal=metal).first()
            if existing:
                existing.bullish_count += bull
                existing.bearish_count += bear
                existing.neutral_count += s.get("neutral", 0)
                existing.total_articles += total
                existing.bullish_ratio = existing.bullish_count / existing.total_articles
                existing.signal = s.get("signal", "MIXED")
            else:
                db.add(SentimentScore(
                    date=today, metal=metal, chemistry=chemistry,
                    bullish_count=bull, bearish_count=bear,
                    neutral_count=s.get("neutral", 0),
                    total_articles=total,
                    bullish_ratio=ratio,
                    signal=s.get("signal", "MIXED"),
                ))


def save_lme_inventory(lme_data: dict):
    """Save LME inventory snapshot."""
    with get_db() as db:
        for metal, data in lme_data.items():
            db.add(LMEInventory(
                metal=metal,
                stock_tonnes=data.get("lme_stock_tonnes", 0),
                signal=data.get("signal", ""),
            ))


def save_forecast(run_id: str, forecast_data: dict, margin_data: dict):
    """Save the agent forecast."""
    with get_db() as db:
        db.add(Forecast(
            run_id=run_id,
            chemistry=margin_data.get("chemistry", ""),
            signal=forecast_data.get("signal", ""),
            full_report=forecast_data.get("full_report", ""),
            buy_decision=margin_data.get("buy_decision"),
            selling_price_inr=margin_data.get("selling_price_inr"),
            max_buy_price_inr=margin_data.get("max_buy_price_inr"),
            aggregator_ask_inr=margin_data.get("aggregator_ask_inr"),
        ))


def save_prediction(run_id: str, pred: dict):
    """Save a price prediction from the Prediction Agent."""
    with get_db() as db:
        db.add(PricePrediction(
            run_id=run_id,
            metal=pred["metal"],
            chemistry=pred["chemistry"],
            current_price_usd=pred["current_price_usd"],
            pred_7d_usd=pred.get("pred_7d_usd"),
            pred_14d_usd=pred.get("pred_14d_usd"),
            pred_30d_usd=pred.get("pred_30d_usd"),
            direction=pred.get("direction"),
            confidence_pct=pred.get("confidence_pct"),
            signal_inputs=pred.get("signal_inputs", {}),
        ))


# ══════════════════════════════════════════════════════════════════════════════
#  READ OPERATIONS — used by Prediction Agent and dashboard endpoints
# ══════════════════════════════════════════════════════════════════════════════

def get_price_history(metal: str, days: int = 30) -> list[dict]:
    """
    Returns daily price history for a metal.
    Used by Prediction Agent to compute trends.
    """
    since = datetime.utcnow() - timedelta(days=days)
    with get_db() as db:
        rows = (
            db.query(MetalPrice)
            .filter(MetalPrice.metal == metal, MetalPrice.scraped_at >= since)
            .order_by(MetalPrice.scraped_at.asc())
            .all()
        )
        return [{
            "date": r.scraped_at.strftime("%Y-%m-%d"),
            "price_usd": r.consensus_price_usd,
            "price_inr_kg": r.price_inr_per_kg,
            "confidence": r.confidence,
            "spread_pct": r.spread_pct,
        } for r in rows]


def get_sentiment_history(metal: str, days: int = 30) -> list[dict]:
    """Returns daily sentiment scores for a metal."""
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    with get_db() as db:
        rows = (
            db.query(SentimentScore)
            .filter(SentimentScore.metal == metal, SentimentScore.date >= since)
            .order_by(SentimentScore.date.asc())
            .all()
        )
        return [{
            "date": r.date,
            "bullish_ratio": r.bullish_ratio,
            "signal": r.signal,
            "total_articles": r.total_articles,
        } for r in rows]


def get_forecast_accuracy(chemistry: str, days: int = 90) -> dict:
    """
    Calculates how accurate past forecasts have been.
    Used by Prediction Agent to self-calibrate confidence.
    """
    since = datetime.utcnow() - timedelta(days=days)
    with get_db() as db:
        total = db.query(func.count(Forecast.id)).filter(
            Forecast.chemistry == chemistry,
            Forecast.generated_at >= since,
            Forecast.was_correct.isnot(None),
        ).scalar()

        correct = db.query(func.count(Forecast.id)).filter(
            Forecast.chemistry == chemistry,
            Forecast.generated_at >= since,
            Forecast.was_correct == True,
        ).scalar()

        recent = (
            db.query(Forecast)
            .filter(Forecast.chemistry == chemistry)
            .order_by(desc(Forecast.generated_at))
            .limit(10)
            .all()
        )

        return {
            "total_evaluated": total or 0,
            "correct": correct or 0,
            "accuracy_pct": round((correct / total * 100), 1) if total else None,
            "recent_signals": [{"signal": f.signal, "date": f.generated_at.strftime("%Y-%m-%d")} for f in recent],
        }


def get_margin_trend(chemistry: str, days: int = 30) -> dict:
    """
    Returns trend in max buy price and margin over time.
    Tells the agent if margins are improving or compressing.
    """
    since = datetime.utcnow() - timedelta(days=days)
    with get_db() as db:
        rows = (
            db.query(MarginAnalysis)
            .filter(
                MarginAnalysis.chemistry == chemistry,
                MarginAnalysis.calculated_at >= since,
            )
            .order_by(MarginAnalysis.calculated_at.asc())
            .all()
        )

        if not rows:
            return {"data_points": 0, "trend": "NO DATA"}

        prices = [r.selling_price_inr for r in rows]
        margins = [r.margin_at_ask_pct for r in rows if r.margin_at_ask_pct is not None]
        buy_signals = [r.buy_decision for r in rows]

        avg_margin = round(sum(margins) / len(margins), 2) if margins else None
        buy_rate = round(sum(buy_signals) / len(buy_signals) * 100, 1) if buy_signals else 0

        price_trend = "STABLE"
        if len(prices) >= 2:
            chg = (prices[-1] - prices[0]) / prices[0] * 100
            price_trend = "RISING" if chg > 3 else "FALLING" if chg < -3 else "STABLE"

        return {
            "data_points": len(rows),
            "avg_selling_price_inr": round(sum(prices) / len(prices), 2),
            "latest_selling_price_inr": prices[-1],
            "avg_margin_pct": avg_margin,
            "price_trend": price_trend,
            "buy_rate_pct": buy_rate,
            "history": [{
                "date": r.calculated_at.strftime("%Y-%m-%d %H:%M"),
                "selling_price_inr": r.selling_price_inr,
                "max_buy_price_inr": r.max_buy_price_inr,
                "ask_inr": r.aggregator_ask_inr,
                "buy_decision": r.buy_decision,
                "margin_pct": r.margin_at_ask_pct,
            } for r in rows],
        }


def get_lme_inventory_trend(metal: str, days: int = 30) -> dict:
    """Returns LME stock trend — rising = bearish, falling = bullish."""
    since = datetime.utcnow() - timedelta(days=days)
    with get_db() as db:
        rows = (
            db.query(LMEInventory)
            .filter(LMEInventory.metal == metal, LMEInventory.recorded_at >= since)
            .order_by(LMEInventory.recorded_at.asc())
            .all()
        )
        if not rows:
            return {"trend": "NO DATA", "data_points": 0}

        stocks = [r.stock_tonnes for r in rows]
        trend = "STABLE"
        if len(stocks) >= 2:
            chg = (stocks[-1] - stocks[0]) / max(stocks[0], 1) * 100
            trend = "RISING" if chg > 5 else "FALLING" if chg < -5 else "STABLE"

        return {
            "trend": trend,
            "current_stock": stocks[-1],
            "data_points": len(stocks),
            "interpretation": "bearish (oversupply)" if trend == "RISING" else "bullish (tight)" if trend == "FALLING" else "neutral",
        }


def get_recent_news(metal: str, days: int = 7, limit: int = 20) -> list[dict]:
    """Fetch recent articles for a metal — used by Prediction Agent."""
    since = datetime.utcnow() - timedelta(days=days)
    with get_db() as db:
        rows = (
            db.query(NewsArticle)
            .filter(NewsArticle.metal == metal, NewsArticle.scraped_at >= since)
            .order_by(desc(NewsArticle.scraped_at))
            .limit(limit)
            .all()
        )
        return [{
            "headline": r.headline,
            "sentiment": r.sentiment,
            "source": r.source,
            "url": r.url,
            "date": r.scraped_at.strftime("%Y-%m-%d"),
            "price_mentions": r.price_mentions,
        } for r in rows]


def get_latest_predictions(chemistry: str, limit: int = 5) -> list[dict]:
    """Get most recent price predictions."""
    with get_db() as db:
        rows = (
            db.query(PricePrediction)
            .filter(PricePrediction.chemistry == chemistry)
            .order_by(desc(PricePrediction.generated_at))
            .limit(limit)
            .all()
        )
        return [{
            "metal": r.metal,
            "generated_at": r.generated_at.strftime("%Y-%m-%d %H:%M"),
            "current_price": r.current_price_usd,
            "pred_7d": r.pred_7d_usd,
            "pred_14d": r.pred_14d_usd,
            "pred_30d": r.pred_30d_usd,
            "direction": r.direction,
            "confidence_pct": r.confidence_pct,
            "signal_inputs": r.signal_inputs,
        } for r in rows]


def get_dashboard_stats() -> dict:
    """Summary stats for the dashboard header."""
    with get_db() as db:
        total_runs = db.query(func.count(MarginAnalysis.id)).scalar() or 0
        total_articles = db.query(func.count(NewsArticle.id)).scalar() or 0
        total_predictions = db.query(func.count(PricePrediction.id)).scalar() or 0

        # Latest prices per metal
        latest_prices = {}
        for metal in ["nickel", "cobalt", "lithium", "lead"]:
            row = (
                db.query(MetalPrice)
                .filter(MetalPrice.metal == metal)
                .order_by(desc(MetalPrice.scraped_at))
                .first()
            )
            if row:
                latest_prices[metal] = {
                    "price": row.consensus_price_usd,
                    "date": row.scraped_at.strftime("%Y-%m-%d %H:%M"),
                    "confidence": row.confidence,
                }

        return {
            "total_runs": total_runs,
            "total_articles": total_articles,
            "total_predictions": total_predictions,
            "latest_prices": latest_prices,
        }


def build_prediction_context(chemistry: str, metals: list[str]) -> dict:
    """
    Assembles the full historical context the Prediction Agent needs.
    This is the key function — it feeds the agent rich data.
    """
    context = {
        "chemistry": chemistry,
        "metals": {},
        "forecast_accuracy": get_forecast_accuracy(chemistry, days=90),
        "margin_trend": get_margin_trend(chemistry, days=30),
    }

    for metal in metals:
        prices = get_price_history(metal, days=30)
        sentiment = get_sentiment_history(metal, days=14)
        inventory = get_lme_inventory_trend(metal, days=30)
        recent_news = get_recent_news(metal, days=7, limit=10)

        # Compute simple momentum: last 7d avg vs prev 7d avg
        momentum = "NEUTRAL"
        if len(prices) >= 14:
            recent_avg = sum(p["price_usd"] for p in prices[-7:]) / 7
            prev_avg = sum(p["price_usd"] for p in prices[-14:-7]) / 7
            chg = (recent_avg - prev_avg) / prev_avg * 100
            momentum = f"UP {abs(chg):.1f}%" if chg > 1 else f"DOWN {abs(chg):.1f}%" if chg < -1 else "FLAT"

        context["metals"][metal] = {
            "price_history": prices,
            "data_points": len(prices),
            "momentum_7d": momentum,
            "sentiment_history": sentiment,
            "lme_inventory": inventory,
            "recent_news_headlines": [n["headline"] for n in recent_news[:5]],
            "recent_sentiments": [n["sentiment"] for n in recent_news],
            "latest_price": prices[-1]["price_usd"] if prices else None,
        }

    return context
