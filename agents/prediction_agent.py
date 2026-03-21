"""
agents/prediction_agent.py — Fixed
Uses historical DB data to forecast prices. Auto-detects NVIDIA or DeepSeek.
Fixed: list type hints, lowercase database imports.
"""

import logging
import os
import statistics
from datetime import datetime

from crewai import Agent, Task
from crewai.tools import BaseTool
from pydantic import BaseModel, Field, SecretStr
from config import NVIDIA_API_KEY, DEEPSEEK_API_KEY

logger = logging.getLogger(__name__)

# ── LLM: Auto-detect based on configured keys
_nvidia_key = NVIDIA_API_KEY or os.getenv("NVIDIA_API_KEY", "")
_deepseek_key = DEEPSEEK_API_KEY or os.getenv("DEEPSEEK_API_KEY", "")

if _nvidia_key:
    from crewai import LLM

    _llm = LLM(
        model="openai/meta/llama-3.3-70b-instruct",
        provider="openai",
        api_key=SecretStr(_nvidia_key),
        base_url="https://integrate.api.nvidia.com/v1",
        temperature=0.1,
        max_tokens=2000,
        timeout=300,
        max_retries=3,
    )
    os.environ["OPENAI_API_KEY"] = _nvidia_key
    os.environ["OPENAI_API_BASE"] = "https://integrate.api.nvidia.com/v1"
    logger.info("Using NVIDIA NIM LLM")

elif _deepseek_key:
    from langchain_deepseek import ChatDeepSeek

    _llm = ChatDeepSeek(
        model="deepseek-chat",
        api_key=SecretStr(_deepseek_key),
        temperature=0.1,
        max_tokens=2000,
    )
    logger.info("Using DeepSeek LLM")

else:
    raise EnvironmentError(
        "No LLM API key found for prediction_agent. Set NVIDIA_API_KEY or DEEPSEEK_API_KEY in .env"
    )




# ── Tool 1: Load historical context ──────────────────────────────────────────

class LoadHistoryInput(BaseModel):
    chemistry: str = Field(description="Battery chemistry: NMC, LCO, LFP, etc.")
    metals: list = Field(description="List of metals to load history for")  # plain list, not list[str]


class LoadHistoricalContextTool(BaseTool):
    name: str = "load_historical_context"
    description: str = (
        "Loads all historical data from the database: 30-day price history, "
        "14-day sentiment scores, LME inventory trends, margin trends. "
        "ALWAYS call this first before making any prediction."
    )
    args_schema: type = LoadHistoryInput

    def _run(self, chemistry: str, metals: list) -> str:
        try:
            # Ensure database module is findable
            import sys
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if root not in sys.path:
                sys.path.insert(0, root)

            # FIXED: lowercase 'database'
            from database.db_service import build_prediction_context
            ctx = build_prediction_context(chemistry, metals)

            lines = [f"HISTORICAL CONTEXT FOR {chemistry}", "=" * 50, ""]

            acc = ctx.get("forecast_accuracy", {})
            if acc.get("total_evaluated", 0) > 0:
                lines.append(
                    f"PAST FORECAST ACCURACY: {acc['accuracy_pct']}% "
                    f"({acc['correct']}/{acc['total_evaluated']} correct)"
                )
            else:
                lines.append("PAST FORECAST ACCURACY: Not enough data yet — run more analyses")

            mt = ctx.get("margin_trend", {})
            lines.append(
                f"MARGIN TREND (30d): {mt.get('price_trend','?')} | "
                f"Avg selling ₹{mt.get('avg_selling_price_inr','?')}/kg | "
                f"Buy rate {mt.get('buy_rate_pct','?')}%"
            )
            lines.append("")

            for metal, data in ctx.get("metals", {}).items():
                lines.append(f"── {metal.upper()} ──")
                lines.append(f"  Data points (30d): {data['data_points']}")

                lp = data.get("latest_price")
                lines.append(f"  Latest price: ${lp:,.0f}/tonne" if lp else "  Latest price: No data yet")
                lines.append(f"  7d Momentum: {data['momentum_7d']}")

                inv = data.get("lme_inventory", {})
                lines.append(
                    f"  LME Inventory: {inv.get('trend','?')} → {inv.get('interpretation','?')}"
                )

                hist = data.get("price_history", [])
                if len(hist) >= 2:
                    first_p = hist[0]["price_usd"]
                    last_p = hist[-1]["price_usd"]
                    chg = (last_p - first_p) / max(first_p, 1) * 100
                    lines.append(f"  30d Change: {chg:+.1f}% (${first_p:,.0f} → ${last_p:,.0f})")
                    price_vals = [h["price_usd"] for h in hist]
                    if len(price_vals) > 2:
                        vol = statistics.stdev(price_vals) / max(statistics.mean(price_vals), 1) * 100
                        lines.append(f"  30d Volatility: {vol:.1f}%")

                sent = data.get("sentiment_history", [])
                if sent:
                    recent_bull = sum(1 for s in sent[-7:] if s.get("signal") == "BULLISH")
                    lines.append(f"  Sentiment (last 7d): {recent_bull}/7 days bullish")

                headlines = data.get("recent_news_headlines", [])
                if headlines:
                    lines.append("  Recent headlines:")
                    for h in headlines[:3]:
                        lines.append(f"    · {h[:80]}")

                lines.append("")

            return "\n".join(lines)

        except Exception as e:
            return (
                f"ERROR loading history: {e}\n"
                "The database may not have enough data yet. "
                "Run the analysis pipeline a few times first to build up history."
            )


# ── Tool 2: Save predictions ──────────────────────────────────────────────────

class SavePredictionInput(BaseModel):
    run_id: str = Field(description="Current run UUID")
    predictions: list = Field(description="List of prediction dicts")


class SavePredictionsTool(BaseTool):
    name: str = "save_predictions"
    description: str = "Saves price predictions to the database for accuracy tracking over time."
    args_schema: type = SavePredictionInput

    def _run(self, run_id: str, predictions: list) -> str:
        try:
            import sys
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if root not in sys.path:
                sys.path.insert(0, root)
            # FIXED: lowercase 'database'
            from database.db_service import save_prediction
            for pred in predictions:
                save_prediction(run_id, pred)
            return f"Saved {len(predictions)} predictions to database successfully."
        except Exception as e:
            return f"Save failed: {e}"


# ── Tool 3: Load past predictions ─────────────────────────────────────────────

class LoadPastPredictionsInput(BaseModel):
    chemistry: str = Field(description="Battery chemistry")


class LoadPastPredictionsTool(BaseTool):
    name: str = "load_past_predictions"
    description: str = "Loads past predictions to check historical accuracy."
    args_schema: type = LoadPastPredictionsInput

    def _run(self, chemistry: str) -> str:
        try:
            import sys
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if root not in sys.path:
                sys.path.insert(0, root)
            # FIXED: lowercase 'database'
            from database.db_service import get_latest_predictions
            preds = get_latest_predictions(chemistry, limit=5)
            if not preds:
                return "No previous predictions found. This is the first prediction for this chemistry."
            lines = ["PAST PREDICTIONS:"]
            for p in preds:
                pred_7d = p.get("pred_7d") or 0
                lines.append(
                    f"  [{p['generated_at']}] {p['metal'].upper()}: "
                    f"${p['current_price']:,.0f} → 7d: ${pred_7d:,.0f} "
                    f"| {p.get('direction','?')} | {p.get('confidence_pct',0):.0f}% conf"
                )
            return "\n".join(lines)
        except Exception as e:
            return f"Load failed: {e}"


# ── Agent & Task ──────────────────────────────────────────────────────────────

load_history_tool     = LoadHistoricalContextTool()
save_predictions_tool = SavePredictionsTool()
load_past_preds_tool  = LoadPastPredictionsTool()

prediction_agent = Agent(
    role="Quantitative Price Prediction Analyst",
    goal=(
        "Use historical price data, sentiment trends, LME inventory patterns, "
        "and margin history from the database to forecast metal prices at "
        "7, 14, and 30 day horizons. Save predictions to the database."
    ),
    backstory=(
        "You are a quantitative analyst who learns from every data point. "
        "You study momentum, sentiment shifts, inventory cycles, and margin compression. "
        "The more historical data, the higher your confidence. "
        "With fewer than 7 days of data, you flag uncertainty and keep confidence below 30%. "
        "You save all predictions so accuracy can be tracked over time."
    ),
    llm=_llm,
    tools=[load_history_tool, save_predictions_tool, load_past_preds_tool],
    verbose=True,
    allow_delegation=False,
    max_iter=5,
)


def make_prediction_task(chemistry: str, metals: list, run_id: str) -> Task:
    return Task(
        description=f"""
Chemistry: {chemistry}
Metals: {', '.join(metals)}
Run ID: {run_id}
Current Date: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}

STEP 1: Call load_past_predictions with chemistry='{chemistry}'
STEP 2: Call load_historical_context with chemistry='{chemistry}' and metals={metals}
STEP 3: Analyze data and generate predictions for each metal.
STEP 4: Call save_predictions with run_id='{run_id}' and your predictions list.

Each prediction dict MUST have:
  metal (str), chemistry (str), current_price_usd (float),
  pred_7d_usd (float), pred_14d_usd (float), pred_30d_usd (float),
  direction ("UP"/"DOWN"/"SIDEWAYS"), confidence_pct (float 0-100),
  signal_inputs (dict: price_trend_30d, momentum_7d, sentiment_signal,
                 lme_inventory_trend, data_points_used, reasoning)

RULES:
- Fewer than 5 data points: confidence_pct MUST be below 30
- Use only what the database shows — not general commodity knowledge
- Be explicit about data limitations
""",
        expected_output=(
            "Predictions saved to DB. Summary of each metal's "
            "7/14/30 day forecast with direction and confidence."
        ),
        agent=prediction_agent,
    )