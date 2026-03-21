"""
database/db_service.py — Fixed version
All calculations done on Python values AFTER fetching from DB.
Never pass SQLAlchemy column objects to round() or Python conditionals.
"""

import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, desc

from database.models import (
    MetalPrice, ForexRate, MarginAnalysis, NewsArticle,
    SentimentScore, LMEInventory, Forecast, PricePrediction,
)
from database.connection import get_db

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  WRITE OPERATIONS
# ══════════════════════════════════════════════════════════════════════════════

def save_forex_rate(run_id: str, rate: float, source: str, stale: bool = False):
    try:
        with get_db() as db:
            db.add(ForexRate(rate=float(rate), source=str(source), stale=bool(stale)))
        logger.debug(f"Saved forex: ₹{rate}")
    except Exception as e:
        logger.error(f"save_forex_rate error: {e}")


def save_metal_prices(run_id: str, chemistry: str, prices_data: dict, usd_inr: float):
    try:
        with get_db() as db:
            for metal, data in prices_data.items():
                if metal.startswith("_") or not isinstance(data, dict):
                    continue
                cp = float(data.get("consensus_price") or 0)
                sources_list = data.get("sources", [])
                source_prices = {}
                for s in sources_list:
                    if isinstance(s, dict):
                        source_prices[str(s.get("source", ""))] = float(s.get("price") or 0)

                price_inr = round((cp / 1000) * float(usd_inr), 4) if cp and usd_inr else None

                row = MetalPrice(
                    run_id=str(run_id),
                    metal=str(metal),
                    chemistry=str(chemistry),
                    consensus_price_usd=cp,
                    source_count=int(data.get("source_count") or 1),
                    spread_pct=float(data.get("spread_pct") or 0.0),
                    confidence=str(data.get("confidence") or "LOW"),
                    source_prices=source_prices,
                    price_inr_per_kg=price_inr,
                    usd_inr_rate=float(usd_inr),
                    chart_data=data.get("chart", []),
                )
                db.add(row)
        logger.debug(f"Saved metal prices for run {run_id[:8]}")
    except Exception as e:
        logger.error(f"save_metal_prices error: {e}")


def save_margin_analysis(run_id: str, analysis_data: dict):
    try:
        with get_db() as db:
            max_buy = float(analysis_data.get("max_buy_price_inr") or 0)
            ask = float(analysis_data.get("aggregator_ask_inr") or 0)
            headroom = max_buy - ask

            row = MarginAnalysis(
                run_id=str(run_id),
                chemistry=str(analysis_data.get("chemistry", "")),
                aggregator_ask_inr=ask,
                usd_inr_rate=float(analysis_data.get("usd_inr_rate") or 0),
                metal_prices_json=analysis_data.get("metal_breakdown", {}),
                gross_metal_value_usd=float(analysis_data.get("gross_metal_value_usd") or 0),
                selling_price_usd=float(analysis_data.get("selling_price_usd") or 0),
                selling_price_inr=float(analysis_data.get("selling_price_inr") or 0),
                max_buy_price_inr=max_buy,
                buy_decision=bool(analysis_data.get("buy_decision", False)),
                margin_at_ask_pct=float(analysis_data["margin_at_ask_pct"]) if analysis_data.get("margin_at_ask_pct") is not None else None,
                headroom_inr=headroom,
                stale_data=bool(analysis_data.get("stale_data", False)),
                warnings=analysis_data.get("warnings", []),
            )
            db.add(row)
        logger.debug(f"Saved margin analysis for {analysis_data.get('chemistry')}")
    except Exception as e:
        logger.error(f"save_margin_analysis error: {e}")


def save_news_articles(run_id: str, articles: list, chemistry: str = ""):
    try:
        saved = 0
        with get_db() as db:
            for a in articles:
                headline = str(a.get("headline", "")).strip()
                if not headline:
                    continue
                h_hash = hashlib.md5(headline.lower().encode()).hexdigest()

                existing = db.query(NewsArticle).filter_by(headline_hash=h_hash).first()
                if existing:
                    continue

                row = NewsArticle(
                    run_id=str(run_id),
                    metal=str(a.get("metal", "unknown")),
                    chemistry=str(chemistry),
                    source=str(a.get("source", "")),
                    headline=headline,
                    url=str(a.get("url", "") or ""),
                    sentiment=str(a.get("sentiment", "neutral")),
                    price_mentions=a.get("price_mentions", []),
                    headline_hash=h_hash,
                )
                db.add(row)
                saved += 1
        logger.debug(f"Saved {saved} new articles")
    except Exception as e:
        logger.error(f"save_news_articles error: {e}")


def save_sentiment(run_id: str, sentiment_data: dict, chemistry: str = ""):
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        with get_db() as db:
            for metal, s in sentiment_data.items():
                total = int(s.get("total_articles") or 0)
                if total == 0:
                    continue
                bull = int(s.get("bullish") or 0)
                bear = int(s.get("bearish") or 0)
                neut = int(s.get("neutral") or 0)
                ratio = round(bull / total, 4) if total > 0 else 0.0

                existing = db.query(SentimentScore).filter_by(date=today, metal=metal).first()
                if existing:
                    existing.bullish_count += bull
                    existing.bearish_count += bear
                    existing.neutral_count += neut
                    existing.total_articles += total
                    new_total = existing.total_articles
                    existing.bullish_ratio = round(existing.bullish_count / new_total, 4) if new_total > 0 else 0.0
                    existing.signal = str(s.get("signal", "MIXED"))
                else:
                    db.add(SentimentScore(
                        date=today,
                        metal=str(metal),
                        chemistry=str(chemistry),
                        bullish_count=bull,
                        bearish_count=bear,
                        neutral_count=neut,
                        total_articles=total,
                        bullish_ratio=ratio,
                        signal=str(s.get("signal", "MIXED")),
                    ))
    except Exception as e:
        logger.error(f"save_sentiment error: {e}")


def save_lme_inventory(lme_data: dict):
    try:
        with get_db() as db:
            for metal, data in lme_data.items():
                db.add(LMEInventory(
                    metal=str(metal),
                    stock_tonnes=float(data.get("lme_stock_tonnes") or 0),
                    signal=str(data.get("signal") or ""),
                ))
    except Exception as e:
        logger.error(f"save_lme_inventory error: {e}")


def save_forecast(run_id: str, forecast_data: dict, margin_data: dict):
    try:
        with get_db() as db:
            db.add(Forecast(
                run_id=str(run_id),
                chemistry=str(margin_data.get("chemistry", "")),
                signal=str(forecast_data.get("signal", "")),
                full_report=str(forecast_data.get("full_report", "") or ""),
                buy_decision=bool(margin_data.get("buy_decision", False)),
                selling_price_inr=float(margin_data["selling_price_inr"]) if margin_data.get("selling_price_inr") is not None else None,
                max_buy_price_inr=float(margin_data["max_buy_price_inr"]) if margin_data.get("max_buy_price_inr") is not None else None,
                aggregator_ask_inr=float(margin_data["aggregator_ask_inr"]) if margin_data.get("aggregator_ask_inr") is not None else None,
            ))
    except Exception as e:
        logger.error(f"save_forecast error: {e}")


def save_prediction(run_id: str, pred: dict):
    try:
        with get_db() as db:
            db.add(PricePrediction(
                run_id=str(run_id),
                metal=str(pred.get("metal", "")),
                chemistry=str(pred.get("chemistry", "")),
                current_price_usd=float(pred.get("current_price_usd") or 0),
                pred_7d_usd=float(pred["pred_7d_usd"]) if pred.get("pred_7d_usd") is not None else None,
                pred_14d_usd=float(pred["pred_14d_usd"]) if pred.get("pred_14d_usd") is not None else None,
                pred_30d_usd=float(pred["pred_30d_usd"]) if pred.get("pred_30d_usd") is not None else None,
                direction=str(pred.get("direction") or ""),
                confidence_pct=float(pred["confidence_pct"]) if pred.get("confidence_pct") is not None else None,
                signal_inputs=pred.get("signal_inputs", {}),
            ))
    except Exception as e:
        logger.error(f"save_prediction error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  READ OPERATIONS
# ══════════════════════════════════════════════════════════════════════════════

def get_price_history(metal: str, days: int = 30) -> list:
    try:
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
                "price_usd": float(r.consensus_price_usd or 0),
                "price_inr_kg": float(r.price_inr_per_kg or 0) if r.price_inr_per_kg else None,
                "confidence": str(r.confidence or ""),
                "spread_pct": float(r.spread_pct or 0),
            } for r in rows]
    except Exception as e:
        logger.error(f"get_price_history error: {e}")
        return []


def get_sentiment_history(metal: str, days: int = 14) -> list:
    try:
        since = (datetime.utcnow() - timedelta(days=days)).date()
        with get_db() as db:
            rows = (
                db.query(SentimentScore)
                .filter(SentimentScore.metal == metal, SentimentScore.date >= since)
                .order_by(SentimentScore.date.asc())
                .all()
            )
            return [{
                "date": str(r.date),
                "bullish_ratio": float(r.bullish_ratio or 0),
                "signal": str(r.signal or ""),
                "total_articles": int(r.total_articles or 0),
            } for r in rows]
    except Exception as e:
        logger.error(f"get_sentiment_history error: {e}")
        return []


def get_forecast_accuracy(chemistry: str, days: int = 90) -> dict:
    try:
        since = datetime.utcnow() - timedelta(days=days)
        with get_db() as db:
            # Count total evaluated forecasts (where was_correct is not None)
            all_rows = (
                db.query(Forecast)
                .filter(
                    Forecast.chemistry == chemistry,
                    Forecast.generated_at >= since,
                    Forecast.was_correct.isnot(None),
                )
                .all()
            )
            total = len(all_rows)
            correct = sum(1 for r in all_rows if r.was_correct is True)

            recent_rows = (
                db.query(Forecast)
                .filter(Forecast.chemistry == chemistry)
                .order_by(desc(Forecast.generated_at))
                .limit(10)
                .all()
            )
            recent = [
                {"signal": str(r.signal), "date": r.generated_at.strftime("%Y-%m-%d")}
                for r in recent_rows
            ]

            accuracy = round(correct / total * 100, 1) if total > 0 else None
            return {
                "total_evaluated": total,
                "correct": correct,
                "accuracy_pct": accuracy,
                "recent_signals": recent,
            }
    except Exception as e:
        logger.error(f"get_forecast_accuracy error: {e}")
        return {"total_evaluated": 0, "correct": 0, "accuracy_pct": None, "recent_signals": []}


def get_margin_trend(chemistry: str, days: int = 30) -> dict:
    try:
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

            # Extract Python floats FIRST, then do math
            prices = [float(r.selling_price_inr or 0) for r in rows]
            margins = [float(r.margin_at_ask_pct) for r in rows if r.margin_at_ask_pct is not None]
            buy_signals = [bool(r.buy_decision) for r in rows]

            avg_margin = round(sum(margins) / len(margins), 2) if margins else None
            buy_rate = round(sum(buy_signals) / len(buy_signals) * 100, 1) if buy_signals else 0.0
            avg_price = round(sum(prices) / len(prices), 2) if prices else 0.0

            price_trend = "STABLE"
            if len(prices) >= 2 and prices[0] > 0:
                chg = (prices[-1] - prices[0]) / prices[0] * 100
                price_trend = "RISING" if chg > 3 else "FALLING" if chg < -3 else "STABLE"

            history = []
            for r in rows:
                history.append({
                    "date": r.calculated_at.strftime("%Y-%m-%d %H:%M"),
                    "selling_price_inr": float(r.selling_price_inr or 0),
                    "max_buy_price_inr": float(r.max_buy_price_inr or 0),
                    "ask_inr": float(r.aggregator_ask_inr or 0),
                    "buy_decision": bool(r.buy_decision),
                    "margin_pct": float(r.margin_at_ask_pct) if r.margin_at_ask_pct is not None else None,
                })

            return {
                "data_points": len(rows),
                "avg_selling_price_inr": avg_price,
                "latest_selling_price_inr": prices[-1] if prices else 0.0,
                "avg_margin_pct": avg_margin,
                "price_trend": price_trend,
                "buy_rate_pct": buy_rate,
                "history": history,
            }
    except Exception as e:
        logger.error(f"get_margin_trend error: {e}")
        return {"data_points": 0, "trend": "ERROR"}


def get_lme_inventory_trend(metal: str, days: int = 30) -> dict:
    try:
        since = datetime.utcnow() - timedelta(days=days)
        with get_db() as db:
            rows = (
                db.query(LMEInventory)
                .filter(LMEInventory.metal == metal, LMEInventory.recorded_at >= since)
                .order_by(LMEInventory.recorded_at.asc())
                .all()
            )
            if not rows:
                return {"trend": "NO DATA", "data_points": 0, "interpretation": "no data"}

            # Extract Python floats first
            stocks = [float(r.stock_tonnes or 0) for r in rows]
            trend = "STABLE"
            if len(stocks) >= 2 and stocks[0] > 0:
                chg = (stocks[-1] - stocks[0]) / stocks[0] * 100
                trend = "RISING" if chg > 5 else "FALLING" if chg < -5 else "STABLE"

            interp = "neutral"
            if trend == "RISING":
                interp = "bearish (oversupply)"
            elif trend == "FALLING":
                interp = "bullish (tight supply)"

            return {
                "trend": trend,
                "current_stock": stocks[-1],
                "data_points": len(stocks),
                "interpretation": interp,
            }
    except Exception as e:
        logger.error(f"get_lme_inventory_trend error: {e}")
        return {"trend": "ERROR", "data_points": 0, "interpretation": "error"}


def get_recent_news(metal: str, days: int = 7, limit: int = 20) -> list:
    try:
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
                "headline": str(r.headline or ""),
                "sentiment": str(r.sentiment or "neutral"),
                "source": str(r.source or ""),
                "url": str(r.url or ""),
                "date": r.scraped_at.strftime("%Y-%m-%d"),
                "price_mentions": r.price_mentions or [],
            } for r in rows]
    except Exception as e:
        logger.error(f"get_recent_news error: {e}")
        return []


def get_latest_predictions(chemistry: str, limit: int = 5) -> list:
    try:
        with get_db() as db:
            rows = (
                db.query(PricePrediction)
                .filter(PricePrediction.chemistry == chemistry)
                .order_by(desc(PricePrediction.generated_at))
                .limit(limit)
                .all()
            )
            return [{
                "metal": str(r.metal or ""),
                "generated_at": r.generated_at.strftime("%Y-%m-%d %H:%M"),
                "current_price": float(r.current_price_usd or 0),
                "pred_7d": float(r.pred_7d_usd) if r.pred_7d_usd is not None else None,
                "pred_14d": float(r.pred_14d_usd) if r.pred_14d_usd is not None else None,
                "pred_30d": float(r.pred_30d_usd) if r.pred_30d_usd is not None else None,
                "direction": str(r.direction or ""),
                "confidence_pct": float(r.confidence_pct) if r.confidence_pct is not None else None,
                "signal_inputs": r.signal_inputs or {},
            } for r in rows]
    except Exception as e:
        logger.error(f"get_latest_predictions error: {e}")
        return []


def get_dashboard_stats() -> dict:
    try:
        with get_db() as db:
            total_runs = db.query(func.count(MarginAnalysis.id)).scalar() or 0
            total_articles = db.query(func.count(NewsArticle.id)).scalar() or 0
            total_predictions = db.query(func.count(PricePrediction.id)).scalar() or 0

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
                        "price": float(row.consensus_price_usd or 0),
                        "date": row.scraped_at.strftime("%Y-%m-%d %H:%M"),
                        "confidence": str(row.confidence or ""),
                    }

            return {
                "total_runs": int(total_runs),
                "total_articles": int(total_articles),
                "total_predictions": int(total_predictions),
                "latest_prices": latest_prices,
            }
    except Exception as e:
        logger.error(f"get_dashboard_stats error: {e}")
        return {"total_runs": 0, "total_articles": 0, "total_predictions": 0, "latest_prices": {}}


def build_prediction_context(chemistry: str, metals: list) -> dict:
    """Assembles full historical context for the Prediction Agent."""
    try:
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

            # Momentum: last 7d avg vs previous 7d avg — on Python floats only
            momentum = "NEUTRAL"
            if len(prices) >= 14:
                recent_vals = [p["price_usd"] for p in prices[-7:]]
                prev_vals = [p["price_usd"] for p in prices[-14:-7]]
                if recent_vals and prev_vals:
                    recent_avg = sum(recent_vals) / len(recent_vals)
                    prev_avg = sum(prev_vals) / len(prev_vals)
                    if prev_avg > 0:
                        chg = (recent_avg - prev_avg) / prev_avg * 100
                        if chg > 1:
                            momentum = f"UP {abs(chg):.1f}%"
                        elif chg < -1:
                            momentum = f"DOWN {abs(chg):.1f}%"
                        else:
                            momentum = "FLAT"

            latest_price = prices[-1]["price_usd"] if prices else None

            context["metals"][metal] = {
                "price_history": prices,
                "data_points": len(prices),
                "momentum_7d": momentum,
                "sentiment_history": sentiment,
                "lme_inventory": inventory,
                "recent_news_headlines": [n["headline"] for n in recent_news[:5]],
                "recent_sentiments": [n["sentiment"] for n in recent_news],
                "latest_price": latest_price,
            }

        return context
    except Exception as e:
        logger.error(f"build_prediction_context error: {e}")
        return {"chemistry": chemistry, "metals": {}, "forecast_accuracy": {}, "margin_trend": {}}
