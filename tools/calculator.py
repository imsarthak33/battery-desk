"""
tools/calculator.py
────────────────────
All financial math is executed here in Python — NEVER by the LLM.
This eliminates floating-point hallucination from the pipeline entirely.
"""

from dataclasses import dataclass
from typing import Optional

from config import (
    RECYCLER_PAYABLE_RATE,
    PROFIT_MARGIN,
    ERROR_BUFFER,
    HUB_COST_INR,
    TONNE_TO_KG,
    BATTERY_CHEMISTRIES,
)


@dataclass
class PriceAnalysis:
    chemistry: str
    full_name: str

    # Raw metal composition
    metal_breakdown_usd: dict[str, float]   # metal -> USD value per kg scrap

    # Step 1: gross metal value
    gross_metal_value_usd: float            # sum of all metal values per kg

    # Step 2: recycler selling price (after payable rate)
    selling_price_usd: float

    # Step 3: INR conversion
    usd_inr_rate: float
    selling_price_inr: float

    # Step 4: max buy price
    max_buy_price_inr: float                # after margin + buffer + hub costs
    margin_stack_inr: float                 # total deductions applied

    # Step 5: decision
    aggregator_ask_inr: float
    buy_decision: bool
    margin_at_ask_pct: Optional[float]      # actual margin if we buy at ask

    # Metadata
    price_source: str
    forex_source: str
    stale_data: bool
    warnings: list[str]


def calculate_max_buy_price(
    chemistry_key: str,
    metal_prices_usd_per_tonne: dict[str, float],
    usd_inr_rate: float,
    aggregator_ask_inr: float,
    recycler_payable_rate: float = RECYCLER_PAYABLE_RATE,
    profit_margin: float = PROFIT_MARGIN,
    error_buffer: float = ERROR_BUFFER,
    hub_cost_inr: float = HUB_COST_INR,
    price_source: str = "unknown",
    forex_source: str = "unknown",
    stale_data: bool = False,
) -> PriceAnalysis:
    """
    Full margin stack calculation executed in Python.

    Steps:
    1. Calculate gross metal value per kg of scrap
    2. Apply payable rate → Recycler Selling Price (USD)
    3. Convert to INR using live forex
    4. Deduct profit margin, error buffer, fixed hub costs → Max Buy Price
    5. Compare against aggregator ask → BUY / STOP
    """

    warnings = []

    if stale_data:
        warnings.append("⚠️  Using stale/fallback prices — validate before trading.")

    if chemistry_key not in BATTERY_CHEMISTRIES:
        raise ValueError(
            f"Unknown chemistry: '{chemistry_key}'. "
            f"Valid options: {list(BATTERY_CHEMISTRIES.keys())}"
        )

    profile = BATTERY_CHEMISTRIES[chemistry_key]
    composition = profile["metals"]  # fraction per kg of scrap

    # ── Step 1: Gross metal value (USD per kg of scrap) ──────────────────────
    breakdown = {}
    for metal, fraction in composition.items():
        price_per_tonne = metal_prices_usd_per_tonne.get(metal, 0.0)
        if price_per_tonne == 0.0:
            warnings.append(f"⚠️  No price found for {metal} — treating as $0.")
        price_per_kg = price_per_tonne / TONNE_TO_KG
        metal_value_usd = fraction * price_per_kg   # USD value of this metal in 1kg scrap
        breakdown[metal] = round(metal_value_usd, 6)

    gross_value_usd = sum(breakdown.values())

    # ── Step 2: Recycler Selling Price ────────────────────────────────────────
    # Recyclers don't get 100% of gross value — smelters apply a payable rate
    # (covers processing losses, smelter margin, transport)
    selling_price_usd = gross_value_usd * recycler_payable_rate

    # ── Step 3: Convert to INR ────────────────────────────────────────────────
    selling_price_inr = selling_price_usd * usd_inr_rate

    # ── Step 4: Max Buy Price ─────────────────────────────────────────────────
    # We must earn PROFIT_MARGIN + ERROR_BUFFER on top of all costs
    # Formula: Max Buy = Selling Price × (1 - margin - buffer) - fixed costs
    total_variable_deduction = profit_margin + error_buffer
    max_buy_price_inr = (selling_price_inr * (1.0 - total_variable_deduction)) - hub_cost_inr
    margin_stack_inr = selling_price_inr - max_buy_price_inr

    # ── Step 5: Buy Decision ──────────────────────────────────────────────────
    buy_decision = aggregator_ask_inr <= max_buy_price_inr

    # What's our ACTUAL margin if we buy at the ask price?
    if aggregator_ask_inr > 0 and selling_price_inr > 0:
        actual_profit_inr = selling_price_inr - aggregator_ask_inr - hub_cost_inr
        margin_at_ask_pct = (actual_profit_inr / selling_price_inr) * 100
    else:
        margin_at_ask_pct = None

    return PriceAnalysis(
        chemistry=chemistry_key,
        full_name=profile["full_name"],
        metal_breakdown_usd={k: round(v, 4) for k, v in breakdown.items()},
        gross_metal_value_usd=round(gross_value_usd, 4),
        selling_price_usd=round(selling_price_usd, 4),
        usd_inr_rate=round(usd_inr_rate, 4),
        selling_price_inr=round(selling_price_inr, 2),
        max_buy_price_inr=round(max_buy_price_inr, 2),
        margin_stack_inr=round(margin_stack_inr, 2),
        aggregator_ask_inr=aggregator_ask_inr,
        buy_decision=buy_decision,
        margin_at_ask_pct=round(margin_at_ask_pct, 2) if margin_at_ask_pct else None,
        price_source=price_source,
        forex_source=forex_source,
        stale_data=stale_data,
        warnings=warnings,
    )


def format_analysis_report(analysis: PriceAnalysis) -> str:
    """Human-readable report for the CrewAI agents and final output."""
    lines = [
        f"═══════════════════════════════════════════════════════",
        f"  BATTERY SCRAP PRICE ANALYSIS — {analysis.chemistry}",
        f"  {analysis.full_name}",
        f"═══════════════════════════════════════════════════════",
        f"",
        f"  DATA SOURCES",
        f"  ├── Metal Prices : {analysis.price_source}",
        f"  ├── Forex Rate   : {analysis.forex_source}",
        f"  └── USD/INR Rate : ₹{analysis.usd_inr_rate:.4f}",
        f"",
        f"  METAL VALUE BREAKDOWN (per kg of scrap)",
    ]

    for metal, val in analysis.metal_breakdown_usd.items():
        lines.append(f"  ├── {metal.capitalize():12s}: ${val:.4f}/kg")

    lines += [
        f"  │",
        f"  ├── Gross Value       : ${analysis.gross_metal_value_usd:.4f}/kg",
        f"  ├── Payable Rate      : {RECYCLER_PAYABLE_RATE*100:.0f}%",
        f"  ├── Selling Price USD : ${analysis.selling_price_usd:.4f}/kg",
        f"  └── Selling Price INR : ₹{analysis.selling_price_inr:.2f}/kg",
        f"",
        f"  MARGIN STACK",
        f"  ├── Selling Price INR : ₹{analysis.selling_price_inr:.2f}",
        f"  ├── Profit Margin     : −₹{analysis.selling_price_inr * PROFIT_MARGIN:.2f}  ({PROFIT_MARGIN*100:.0f}%)",
        f"  ├── Error Buffer      : −₹{analysis.selling_price_inr * ERROR_BUFFER:.2f}   ({ERROR_BUFFER*100:.0f}%)",
        f"  ├── Hub Costs Fixed   : −₹{HUB_COST_INR:.2f}",
        f"  └── MAX BUY PRICE     : ₹{analysis.max_buy_price_inr:.2f}/kg",
        f"",
        f"  PROCUREMENT DECISION",
        f"  ├── Aggregator Ask    : ₹{analysis.aggregator_ask_inr:.2f}/kg",
        f"  ├── Max Buy Price     : ₹{analysis.max_buy_price_inr:.2f}/kg",
        f"  ├── Headroom          : ₹{analysis.max_buy_price_inr - analysis.aggregator_ask_inr:.2f}/kg",
    ]

    if analysis.margin_at_ask_pct is not None:
        lines.append(f"  ├── Margin @ Ask      : {analysis.margin_at_ask_pct:.1f}%")

    decision_str = "✅  BUY" if analysis.buy_decision else "🛑  HARD STOP — DO NOT BUY"
    lines += [
        f"  └── DECISION          : {decision_str}",
        f"",
    ]

    if analysis.warnings:
        lines.append("  WARNINGS")
        for w in analysis.warnings:
            lines.append(f"  ! {w}")
        lines.append("")

    lines.append("═══════════════════════════════════════════════════════")
    return "\n".join(lines)
