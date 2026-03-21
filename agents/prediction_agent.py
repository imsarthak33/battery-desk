"""
agents/prediction_agent.py
───────────────────────────
The 4th Agent — Price Prediction.

Unlike the other agents who work on CURRENT data,
this agent uses the DATABASE (historical prices, sentiment, margins, inventory)
to predict future prices at 7, 14, and 30 day horizons.

It gets richer and more accurate every time you run the system
because it learns from more historical data points.
"""

import json
import logging
import os
import statistics
from datetime import datetime

from crewai import Agent, Task, LLM
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Use the NVIDIA-backed OpenAI-compatible model pipeline
# to keep your whole stack on NVIDIA NIM (as configured in agents_and_tasks_v2)
# Ensure environment variable NVIDIA_API_KEY is set
nvidia_prediction_llm = LLM(
    model="openai/gpt-4.1-mini",
    provider="openai",
    api_key=os.environ.get("NVIDIA_API_KEY", ""),
    temperature=0.1,
    max_tokens=2000,
)


# ── Tool: Load historical context from DB ─────────────────────────────────────

class LoadHistoryInput(BaseModel):
    chemistry: str = Field(description="Battery chemistry: NMC, LCO, LFP, etc.")
    metals: list[str] = Field(description="List of metals to load history for")

class LoadHistoricalContextTool(BaseTool):
    name: str = "load_historical_context"
    description: str = (
        "Loads all historical data from the database: 30-day price history, "
        "14-day sentiment scores, LME inventory trends, margin trends, "
        "and forecast accuracy stats. Call this first before making any prediction."
    )
    args_schema: type[BaseModel] = LoadHistoryInput

    def _run(self, chemistry: str, metals: list[str]) -> str:
        try:
            from database.db_service import build_prediction_context
            ctx = build_prediction_context(chemistry, metals)

            # Format a readable summary for the LLM
            lines = [f"HISTORICAL CONTEXT FOR {chemistry}", "=" * 50, ""]

            # Forecast accuracy
            acc = ctx.get("forecast_accuracy", {})
            if acc.get("total_evaluated", 0) > 0:
                lines.append(f"PAST FORECAST ACCURACY: {acc['accuracy_pct']}% ({acc['correct']}/{acc['total_evaluated']} correct)")
            else:
                lines.append("PAST FORECAST ACCURACY: Insufficient data (< 1 week of history)")

            # Margin trend
            mt = ctx.get("margin_trend", {})
            lines.append(f"MARGIN TREND (30d): {mt.get('price_trend','?')} | Avg selling ₹{mt.get('avg_selling_price_inr','?')}/kg | Buy rate {mt.get('buy_rate_pct','?')}%")
            lines.append("")

            # Per metal
            for metal, data in ctx.get("metals", {}).items():
                lines.append(f"── {metal.upper()} ──")
                lines.append(f"  Data points (30d): {data['data_points']}")
                lines.append(f"  Latest price: ${data['latest_price']:,.0f}/tonne" if data['latest_price'] else "  Latest price: No data yet")
                lines.append(f"  7d Momentum: {data['momentum_7d']}")
                lines.append(f"  LME Inventory: {data['lme_inventory'].get('trend','?')} → {data['lme_inventory'].get('interpretation','?')}")

                # Price trend from history
                hist = data.get("price_history", [])
                if len(hist) >= 2:
                    first_p = hist[0]["price_usd"]
                    last_p = hist[-1]["price_usd"]
                    chg_pct = (last_p - first_p) / first_p * 100
                    lines.append(f"  30d Price Change: {chg_pct:+.1f}% (${first_p:,.0f} → ${last_p:,.0f})")

                    # Volatility
                    prices = [h["price_usd"] for h in hist]
                    if len(prices) > 2:
                        vol = statistics.stdev(prices) / statistics.mean(prices) * 100
                        lines.append(f"  30d Volatility: {vol:.1f}%")

                # Sentiment trend
                sent_hist = data.get("sentiment_history", [])
                if sent_hist:
                    recent_bull = sum(1 for s in sent_hist[-7:] if s["signal"] == "BULLISH")
                    lines.append(f"  Sentiment (last 7 days): {recent_bull}/7 days bullish")

                # News headlines
                headlines = data.get("recent_news_headlines", [])
                if headlines:
                    lines.append(f"  Recent headlines:")
                    for h in headlines[:3]:
                        lines.append(f"    · {h[:90]}")

                lines.append("")

            return "\n".join(lines)

        except Exception as e:
            return f"ERROR loading history: {e}\nNote: System may not have enough historical data yet. Run analysis a few times first."


# ── Tool: Save predictions to DB ──────────────────────────────────────────────

class SavePredictionInput(BaseModel):
    run_id: str = Field(description="Current run UUID")
    predictions: list[dict] = Field(description="List of prediction dicts")

class SavePredictionsTool(BaseTool):
    name: str = "save_predictions"
    description: str = "Saves your price predictions to the database for tracking accuracy over time."
    args_schema: type[BaseModel] = SavePredictionInput

    def _run(self, run_id: str, predictions: list[dict]) -> str:
        try:
            from database.db_service import save_prediction
            for pred in predictions:
                save_prediction(run_id, pred)
            return f"Saved {len(predictions)} predictions to database."
        except Exception as e:
            return f"Save failed: {e}"


# ── Tool: Load previous predictions ──────────────────────────────────────────

class LoadPastPredictionsInput(BaseModel):
    chemistry: str = Field(description="Battery chemistry")

class LoadPastPredictionsTool(BaseTool):
    name: str = "load_past_predictions"
    description: str = "Loads your past predictions to see what you predicted vs what actually happened (accuracy tracking)."
    args_schema: type[BaseModel] = LoadPastPredictionsInput

    def _run(self, chemistry: str) -> str:
        try:
            from database.db_service import get_latest_predictions
            preds = get_latest_predictions(chemistry, limit=5)
            if not preds:
                return "No previous predictions found. This is your first prediction for this chemistry."
            lines = ["PAST PREDICTIONS:"]
            for p in preds:
                lines.append(f"  [{p['generated_at']}] {p['metal'].upper()}: "
                              f"Current ${p['current_price']:,.0f} → 7d ${p['pred_7d']:,.0f} | {p['direction']} | {p['confidence_pct']:.0f}% confidence")
            return "\n".join(lines)
        except Exception as e:
            return f"Load failed: {e}"


# ── The Prediction Agent ──────────────────────────────────────────────────────

load_history_tool     = LoadHistoricalContextTool()
save_predictions_tool = SavePredictionsTool()
load_past_preds_tool  = LoadPastPredictionsTool()

prediction_agent = Agent(
    role="Quantitative Price Prediction Analyst",
    goal=(
        "Use historical price data, sentiment trends, LME inventory patterns, "
        "and margin history from the database to forecast metal prices at 7, 14, and 30 day horizons. "
        "Generate structured predictions and save them to the database."
    ),
    backstory=(
        "You are a quantitative analyst who learns from every data point the system has collected. "
        "You study momentum, sentiment shifts, inventory cycles, and margin compression patterns. "
        "The more historical data available, the more confident your predictions. "
        "You are honest about uncertainty — if there are fewer than 7 days of data, "
        "you say so and provide wider confidence ranges. "
        "You save all predictions to the database so accuracy can be tracked over time. "
        "Your predictions improve every week as the database grows."
    ),
    llm=nvidia_prediction_llm,
    tools=[load_history_tool, save_predictions_tool, load_past_preds_tool],
    verbose=True,
    allow_delegation=False,
    max_iter=5,
)


def make_prediction_task(chemistry: str, metals: list[str], run_id: str) -> Task:
    return Task(
        description=f"""
Chemistry: {chemistry}
Metals: {', '.join(metals)}
Run ID: {run_id}
Current Date: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}

STEP 1: Call load_past_predictions with chemistry='{chemistry}'
STEP 2: Call load_historical_context with chemistry='{chemistry}' and metals={metals}
STEP 3: Analyze the data and generate predictions for each metal.
STEP 4: Call save_predictions with run_id='{run_id}' and your predictions list.

For each metal, your prediction dict must include:
  - metal: string
  - chemistry: "{chemistry}"
  - current_price_usd: float
  - pred_7d_usd: float
  - pred_14d_usd: float
  - pred_30d_usd: float
  - direction: "UP" | "DOWN" | "SIDEWAYS"
  - confidence_pct: float (0-100, be honest — low data = low confidence)
  - signal_inputs: dict with keys:
      price_trend_30d, momentum_7d, sentiment_signal,
      lme_inventory_trend, data_points_used, reasoning

IMPORTANT RULES:
- If fewer than 5 data points exist: set confidence_pct < 30 and note "insufficient history"
- Base predictions on actual data — not market knowledge from training
- Predictions should reflect what the DATA shows, not generic commodity opinions
""",
        expected_output=(
            "Predictions saved to DB + a formatted summary showing each metal's "
            "7/14/30 day forecast with direction, confidence, and reasoning."
        ),
        agent=prediction_agent,
    )
