"""
main_v2.py
───────────
Production entry point using the full scraper pipeline.

Usage:
  python main_v2.py                             # NMC at default ask
  python main_v2.py --chemistry LCO --ask 450
  python main_v2.py --chemistry LEAD_ACID --ask 38
  python main_v2.py --all

What's new vs main.py:
  - Uses multi-source scraper pipeline (investing.com, TE, MCX, Reuters, Mining.com, ET, LME)
  - Consensus price with confidence scoring
  - Structured news intelligence instead of raw search snippets
  - Richer Rich console output with confidence indicators
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from crewai import Crew, Process
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import print as rprint

from config import BATTERY_CHEMISTRIES, AGGREGATOR_ASK_INR
from agents_and_tasks_v2 import (
    make_tasks_v2,
    data_fetcher_agent,
    margin_calculator_agent,
    market_forecaster_agent,
)

# ── Logging ─────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("battery_analyzer_v2.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)
console = Console()

REPORTS_DIR = Path("reports")
REPORTS_DIR.mkdir(exist_ok=True)


def run_analysis(chemistry: str, aggregator_ask_inr: float) -> dict:
    """Run the full pipeline for one chemistry type."""

    profile = BATTERY_CHEMISTRIES[chemistry]

    console.print(Panel(
        f"[bold cyan]Analyzing: {chemistry}[/bold cyan]\n"
        f"[dim]{profile['full_name']}[/dim]\n"
        f"[dim]Ask: ₹{aggregator_ask_inr}/kg | Metals: {', '.join(profile['metals'].keys())}[/dim]",
        border_style="cyan",
    ))

    tasks = make_tasks_v2(chemistry, aggregator_ask_inr)

    crew = Crew(
        agents=[data_fetcher_agent, margin_calculator_agent, market_forecaster_agent],
        tasks=tasks,
        process=Process.sequential,
        verbose=False,
        max_rpm=8,
    )

    try:
        result = crew.kickoff()
        return {
            "chemistry": chemistry,
            "full_name": profile["full_name"],
            "aggregator_ask_inr": aggregator_ask_inr,
            "raw_output": str(result),
            "timestamp": datetime.utcnow().isoformat(),
            "status": "success",
        }
    except Exception as e:
        logger.error(f"Crew failed for {chemistry}: {e}", exc_info=True)
        return {
            "chemistry": chemistry,
            "full_name": profile["full_name"],
            "aggregator_ask_inr": aggregator_ask_inr,
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat(),
            "status": "error",
        }


def save_report(results: list[dict]) -> Path:
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"analysis_v2_{ts}.json"
    with open(path, "w") as f:
        json.dump(results, f, indent=2)
    return path


def print_summary_table(results: list[dict]):
    table = Table(
        title="🔋 Battery Scrap Analysis Summary",
        header_style="bold magenta",
        show_lines=True,
    )
    table.add_column("Chemistry",   style="cyan",  width=14)
    table.add_column("Full Name",   style="white", width=35)
    table.add_column("Ask (₹/kg)", justify="right")
    table.add_column("Status",      justify="center", width=12)

    for r in results:
        status_text = Text("✅ OK", style="green") if r["status"] == "success" else Text("❌ ERROR", style="red")
        table.add_row(
            r["chemistry"],
            r.get("full_name", ""),
            f"₹{r['aggregator_ask_inr']:.2f}",
            status_text,
        )
    console.print(table)


def print_scraper_info():
    """Show what data sources are being used."""
    console.print(Panel(
        "[bold]Data Sources Active:[/bold]\n"
        "  💹 [cyan]investing.com[/cyan]         — commodity spot prices (scraped)\n"
        "  📊 [cyan]tradingeconomics.com[/cyan]  — commodity prices + guest API\n"
        "  🏦 [cyan]MCX India[/cyan]             — INR prices for Ni, Pb (official exchange)\n"
        "  📰 [cyan]Reuters[/cyan]               — metal market news & price mentions\n"
        "  ⛏️  [cyan]mining.com[/cyan]            — battery metals coverage\n"
        "  🇮🇳 [cyan]Economic Times[/cyan]        — India-specific signals\n"
        "  🏭 [cyan]LME Notices[/cyan]           — public inventory reports\n"
        "  💱 [cyan]open.er-api.com[/cyan]       — live USD/INR forex\n\n"
        "[dim]Prices aggregated with outlier removal and confidence scoring.[/dim]",
        title="[bold green]Multi-Source Scraper Pipeline[/bold green]",
        border_style="green",
    ))


def main():
    parser = argparse.ArgumentParser(
        description="Battery Scrap Analyzer v2 — Multi-Source Scraper Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main_v2.py --chemistry NMC --ask 300
  python main_v2.py --chemistry LCO --ask 450
  python main_v2.py --chemistry LEAD_ACID --ask 38
  python main_v2.py --chemistry LFP --ask 120
  python main_v2.py --all
        """
    )
    parser.add_argument("--chemistry", choices=list(BATTERY_CHEMISTRIES.keys()))
    parser.add_argument("--ask", type=float, default=AGGREGATOR_ASK_INR)
    parser.add_argument("--all", action="store_true", help="Run all 5 chemistries")
    args = parser.parse_args()

    # Banner
    console.print(Panel(
        "[bold green]🔋 Battery Scrap Price Analyzer v2[/bold green]\n"
        "[dim]investing.com · tradingeconomics.com · MCX India · Reuters · Mining.com · ET · LME[/dim]\n"
        f"[dim]{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}[/dim]",
        border_style="green",
        padding=(1, 4),
    ))

    print_scraper_info()

    # Determine scope
    if args.all:
        to_run = [(c, AGGREGATOR_ASK_INR) for c in BATTERY_CHEMISTRIES]
    elif args.chemistry:
        to_run = [(args.chemistry, args.ask)]
    else:
        to_run = [("NMC", args.ask)]

    # Run pipeline
    results = []
    for chemistry, ask in to_run:
        result = run_analysis(chemistry, ask)
        results.append(result)

        if result["status"] == "success":
            console.print(Panel(
                result["raw_output"],
                title=f"[bold]{chemistry} — Final Report[/bold]",
                border_style="blue",
            ))
        else:
            console.print(f"[bold red]❌ {chemistry} failed:[/bold red] {result.get('error')}")

    # Summary
    console.rule("[bold]Analysis Complete[/bold]")
    print_summary_table(results)
    path = save_report(results)
    console.print(f"\n[dim]📄 Report saved: {path}[/dim]\n")


if __name__ == "__main__":
    main()
