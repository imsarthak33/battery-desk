"""
agents_and_tasks_v2.py
───────────────────────
Updated CrewAI agents and tasks using the full scraper pipeline.

Agent 1 now runs:  investing.com + tradingeconomics.com + MCX + API (parallel)
Agent 2 now runs:  Pure Python margin calculator (no LLM math)
Agent 3 now runs:  Reuters + Mining.com + ET + LME inventory → structured signals
"""

import json
import os

from crewai import Agent, Task, LLM

from config import NVIDIA_API_KEY, SERPER_API_KEY, BATTERY_CHEMISTRIES
from tools.crewai_tools_v2 import (
    AggregatePricesTool,
    FetchForexRateTool,
    RunMarginCalculationTool,
    GetMarketIntelligenceTool,
    ListChemistriesTool,
)

os.environ["SERPER_API_KEY"] = SERPER_API_KEY
# LiteLLM needs the API key in env for OpenAI-compatible providers
os.environ["OPENAI_API_KEY"] = NVIDIA_API_KEY

from langchain_nvidia_ai_endpoints import ChatNVIDIA

# ── LLM ───────────────────────────────────────────────────────────────────────

# Use LangChain's official NVIDIA integration instead of litellm to bypass connection errors
nvidia_brain = ChatNVIDIA(
    model="meta/llama-3.3-70b-instruct",
    api_key=NVIDIA_API_KEY,
    temperature=0,
    max_tokens=2000,
    timeout=120,    
    max_retries=5,
)

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
        "Fetch live metal prices from ALL available sources simultaneously and get the "
        "live forex rate. Return the consensus prices with confidence ratings."
    ),
    backstory=(
        "You are a senior market data engineer. You run parallel scrapers across "
        "investing.com, tradingeconomics.com, MCX India, and paid APIs simultaneously. "
        "You NEVER guess prices. You call aggregate_metal_prices and fetch_usd_inr_rate "
        "and return their exact output. You flag low-confidence data prominently."
    ),
    llm=nvidia_brain,
    tools=[aggregate_prices_tool, fetch_forex_tool, list_chem_tool],
    verbose=True,
    allow_delegation=False,
    max_iter=4,
)

margin_calculator_agent = Agent(
    role="Financial Margin Calculator",
    goal=(
        "Take aggregated price data and run the Python margin calculator to produce "
        "the exact Max Buy Price and BUY/STOP decision."
    ),
    backstory=(
        "You are a precision financial controller. You NEVER do arithmetic in your head. "
        "You take the exact JSON output from the data fetcher and call run_margin_calculation. "
        "You include confidence warnings in your output. You trust Python, not your own math."
    ),
    llm=nvidia_brain,
    tools=[run_calc_tool],
    verbose=True,
    allow_delegation=False,
    max_iter=3,
)

market_forecaster_agent = Agent(
    role="Commodity Intelligence Analyst",
    goal=(
        "Use the structured market intelligence tool to get sentiment, price signals, "
        "and LME inventory data. Produce a SELL TODAY or HOLD INVENTORY decision "
        "with specific data-backed justification."
    ),
    backstory=(
        "You are a seasoned commodity analyst. You use get_market_intelligence to get "
        "pre-processed signals from Reuters, Mining.com, Economic Times, and LME notices. "
        "You interpret sentiment scores, inventory levels, and price trends. "
        "Your recommendation is always backed by specific numbers from the tool output — "
        "never vague or generic. You cite the source for every claim."
    ),
    llm=nvidia_brain,
    tools=[market_intelligence_tool],
    verbose=True,
    allow_delegation=False,
    max_iter=4,
)


# ── Task Factory ───────────────────────────────────────────────────────────────

def make_tasks_v2(chemistry: str, aggregator_ask_inr: float) -> list[Task]:
    """Creates the 3-task pipeline for a given chemistry."""

    metals = list(BATTERY_CHEMISTRIES[chemistry]["metals"].keys())
    metals_str = ", ".join(metals)

    task_fetch = Task(
        description=(
            f"Chemistry: {chemistry} | Metals needed: {metals_str}\n\n"
            "Step 1: Call fetch_usd_inr_rate → get live USD/INR rate.\n"
            "Step 2: Call aggregate_metal_prices with:\n"
            f"  - metals = {metals}\n"
            "  - usd_inr_rate = the rate from step 1\n\n"
            "Return a JSON block containing:\n"
            "  - The 'prices' dict from aggregate_metal_prices\n"
            "  - The live usd_inr_rate\n"
            "  - The confidence levels for each metal\n"
            "  - Any metals that failed to fetch (flag prominently)\n"
            "DO NOT modify, estimate, or interpolate any prices."
        ),
        expected_output=(
            "JSON with aggregated prices (USD/tonne with confidence ratings) "
            "and live USD/INR rate."
        ),
        agent=data_fetcher_agent,
    )

    task_calculate = Task(
        description=(
            f"Chemistry: {chemistry} | Aggregator ask: ₹{aggregator_ask_inr}/kg\n\n"
            "Call run_margin_calculation with:\n"
            f"  - chemistry = '{chemistry}'\n"
            "  - aggregated_prices_json = the 'prices' JSON string from the previous task\n"
            "  - usd_inr_rate = the live rate from the previous task\n"
            f"  - aggregator_ask_inr = {aggregator_ask_inr}\n\n"
            "Return the COMPLETE formatted report including:\n"
            "  - Full margin stack breakdown\n"
            "  - Max Buy Price in INR\n"
            "  - BUY or HARD STOP decision\n"
            "  - All confidence warnings\n"
            "DO NOT calculate anything yourself."
        ),
        expected_output=(
            "Complete margin analysis report from run_margin_calculation with "
            "Max Buy Price and BUY/STOP decision."
        ),
        agent=margin_calculator_agent,
        context=[task_fetch],
    )

    task_forecast = Task(
        description=(
            f"Chemistry: {chemistry} | Metals: {metals_str}\n\n"
            f"Call get_market_intelligence with metals = {metals}\n\n"
            "Analyze the structured intelligence report returned by the tool.\n"
            "Your final output MUST include:\n"
            "1. FINAL DECISION (first line): 'SELL TODAY' or 'HOLD INVENTORY'\n"
            "2. Sentiment summary: bullish/bearish counts per metal\n"
            "3. 2-3 specific price figures cited from the tool output\n"
            "4. LME inventory signal if available\n"
            "5. 3-sentence rationale citing specific sources\n\n"
            "Base everything on what the tool returned. If data is missing, say so explicitly."
        ),
        expected_output=(
            "SELL TODAY or HOLD INVENTORY decision with data-backed justification "
            "citing specific sources, price levels, and inventory signals."
        ),
        agent=market_forecaster_agent,
    )

    return [task_fetch, task_calculate, task_forecast]
