"""
agents_and_tasks_v2.py — Fixed
Auto-detects NVIDIA NIM or DeepSeek based on which key is available.
"""

import os
from crewai import Agent, Task
from config import NVIDIA_API_KEY, DEEPSEEK_API_KEY, SERPER_API_KEY, BATTERY_CHEMISTRIES
from tools.crewai_tools_v2 import (
    AggregatePricesTool,
    FetchForexRateTool,
    RunMarginCalculationTool,
    GetMarketIntelligenceTool,
    ListChemistriesTool,
)

os.environ["SERPER_API_KEY"] = SERPER_API_KEY
os.environ["LITELLM_LOGGING"] = "False"
os.environ["LITELLM_TELEMETRY"] = "False"

# ── LLM: Auto-detect NVIDIA NIM or DeepSeek ───────────────────────────────────
if NVIDIA_API_KEY:
    from crewai import LLM
    brain = LLM(
        model="openai/meta/llama-3.3-70b-instruct",
        provider="openai",
        api_key=NVIDIA_API_KEY,
        base_url="https://integrate.api.nvidia.com/v1",
        temperature=0,
        max_tokens=2000,
        timeout=300,
        max_retries=5,
    )
    os.environ["OPENAI_API_KEY"] = NVIDIA_API_KEY
    os.environ["OPENAI_API_BASE"] = "https://integrate.api.nvidia.com/v1"
    print("🟢 Using NVIDIA NIM (Llama 3.3 70B)")
else:
    from langchain_deepseek import ChatDeepSeek
    brain = ChatDeepSeek(
        model="deepseek-chat",
        api_key=DEEPSEEK_API_KEY,
        temperature=0,
        max_tokens=2000,
    )
    print("🟢 Using DeepSeek")

# ── Tool Instances ─────────────────────────────────────────────────────────────
aggregate_prices_tool    = AggregatePricesTool()
fetch_forex_tool         = FetchForexRateTool()
run_calc_tool            = RunMarginCalculationTool()
market_intelligence_tool = GetMarketIntelligenceTool()
list_chem_tool           = ListChemistriesTool()

# ── Agents ─────────────────────────────────────────────────────────────────────
data_fetcher_agent = Agent(
    role="Market Data Aggregator",
    goal=(
        "Fetch live metal prices from ALL available sources simultaneously "
        "and get the live forex rate. Return consensus prices with confidence ratings."
    ),
    backstory=(
        "You are a senior market data engineer. You run parallel scrapers across "
        "investing.com, tradingeconomics.com, MCX India, and paid APIs. "
        "You NEVER guess prices. Always call aggregate_metal_prices and fetch_usd_inr_rate. "
        "Flag low-confidence data prominently."
    ),
    llm=brain,
    tools=[aggregate_prices_tool, fetch_forex_tool, list_chem_tool],
    verbose=True,
    allow_delegation=False,
    max_iter=4,
)

margin_calculator_agent = Agent(
    role="Financial Margin Calculator",
    goal=(
        "Take aggregated price data and run the Python margin calculator "
        "to produce the exact Max Buy Price and BUY/STOP decision."
    ),
    backstory=(
        "You are a precision financial controller. NEVER do arithmetic yourself. "
        "Take the exact JSON from the data fetcher and call run_margin_calculation. "
        "Trust Python math, not your own estimates."
    ),
    llm=brain,
    tools=[run_calc_tool],
    verbose=True,
    allow_delegation=False,
    max_iter=3,
)

market_forecaster_agent = Agent(
    role="Commodity Intelligence Analyst",
    goal=(
        "Use the structured market intelligence tool to get sentiment, price signals, "
        "and LME inventory data. Produce SELL TODAY or HOLD INVENTORY with data backing."
    ),
    backstory=(
        "You are a seasoned commodity analyst. Use get_market_intelligence for "
        "pre-processed signals from Reuters, Mining.com, ET, and LME notices. "
        "Every claim must cite a specific source and number from the tool output."
    ),
    llm=brain,
    tools=[market_intelligence_tool],
    verbose=True,
    allow_delegation=False,
    max_iter=4,
)


def make_tasks_v2(chemistry: str, aggregator_ask_inr: float) -> list:
    metals = list(BATTERY_CHEMISTRIES[chemistry]["metals"].keys())
    metals_str = ", ".join(metals)

    task_fetch = Task(
        description=(
            f"Chemistry: {chemistry} | Metals needed: {metals_str}\n\n"
            "Step 1: Call fetch_usd_inr_rate → get live USD/INR rate.\n"
            "Step 2: Call aggregate_metal_prices with:\n"
            f"  metals = {metals}\n"
            "  usd_inr_rate = rate from step 1\n\n"
            "Return JSON with prices dict, live usd_inr_rate, confidence levels.\n"
            "DO NOT modify or estimate any prices."
        ),
        expected_output="JSON with aggregated prices (USD/tonne) and live USD/INR rate.",
        agent=data_fetcher_agent,
    )

    task_calculate = Task(
        description=(
            f"Chemistry: {chemistry} | Ask: ₹{aggregator_ask_inr}/kg\n\n"
            "Call run_margin_calculation with:\n"
            f"  chemistry = '{chemistry}'\n"
            "  aggregated_prices_json = prices JSON from previous task\n"
            "  usd_inr_rate = live rate from previous task\n"
            f"  aggregator_ask_inr = {aggregator_ask_inr}\n\n"
            "Return COMPLETE formatted report with Max Buy Price and BUY/STOP decision.\n"
            "DO NOT calculate anything yourself."
        ),
        expected_output="Complete margin report with Max Buy Price and BUY/STOP decision.",
        agent=margin_calculator_agent,
        context=[task_fetch],
    )

    task_forecast = Task(
        description=(
            f"Chemistry: {chemistry} | Metals: {metals_str}\n\n"
            f"Call get_market_intelligence with metals = {metals}\n\n"
            "Analyze the structured intelligence report.\n"
            "Output MUST include:\n"
            "1. FINAL DECISION (first line): 'SELL TODAY' or 'HOLD INVENTORY'\n"
            "2. Sentiment summary per metal\n"
            "3. 2-3 specific price figures from tool output\n"
            "4. LME inventory signal if available\n"
            "5. 3-sentence rationale with sources cited\n\n"
            "If data is missing, say so explicitly."
        ),
        expected_output="SELL TODAY or HOLD INVENTORY + data-backed justification.",
        agent=market_forecaster_agent,
    )

    return [task_fetch, task_calculate, task_forecast]
    