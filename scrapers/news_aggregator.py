"""
scrapers/news_aggregator.py
────────────────────────────
Runs all news scrapers and produces a structured market intelligence report
that the CrewAI forecaster agent can use directly.

Instead of the agent guessing from raw search snippets,
it gets: price mentions, sentiment scores, inventory signals, and ranked headlines.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

from scrapers.news_scraper import (
    ReutersMetalsScraper,
    MiningDotComScraper,
    EconomicTimesScraper,
    LMENoticesScraper,
    BULLISH_KEYWORDS,
    BEARISH_KEYWORDS,
)
from scrapers.api_news_scraper import fetch_all_news

logger = logging.getLogger(__name__)


class NewsAggregator:

    def __init__(self, metals: list[str]):
        self.metals = metals

    def fetch_all(self) -> dict:
        """
        Runs all news scrapers in parallel.
        Returns structured market intelligence:
        {
          "articles": [...],
          "sentiment_summary": {
              "nickel": {"bullish": 5, "bearish": 2, "neutral": 1, "signal": "BULLISH"},
              ...
          },
          "price_mentions": [...],   # concrete price figures from articles
          "lme_inventory": {...},    # LME stock signals
          "top_headlines": [...],    # most relevant 10 headlines
          "recommendation_inputs": "...",  # pre-formatted text for the LLM agent
        }
        """
        scrapers = {
            "reuters": lambda: ReutersMetalsScraper(metals=self.metals).scrape(),
            "mining.com": lambda: MiningDotComScraper(metals=self.metals).scrape(),
            "economic_times": lambda: EconomicTimesScraper(metals=self.metals).scrape(),
            "lme_notices": lambda: LMENoticesScraper().scrape(),
            "api_news": lambda: fetch_all_news(metals=self.metals),
        }

        all_articles = []
        lme_inventory = {}

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(fn): name for name, fn in scrapers.items()}
            for future in as_completed(futures, timeout=45):
                name = futures[future]
                try:
                    data = future.result(timeout=30)
                    if name == "lme_notices":
                        lme_inventory = data.get("stocks", {})
                    else:
                        articles = data.get("articles", [])
                        # mining.com also returns articles
                        all_articles.extend(articles)
                        logger.info(f"✓ {name}: {len(articles)} articles")
                except Exception as e:
                    logger.warning(f"✗ {name}: {e}")

        # If everything was blocked (common on Railway IPs), fallback to Serper API
        if len(all_articles) == 0:
            logger.warning("All free scrapers blocked. Falling back to Serper API.")
            import os
            import requests
            import json
            serper_key = os.environ.get("SERPER_API_KEY", "")
            if serper_key:
                headers = {'X-API-KEY': serper_key, 'Content-Type': 'application/json'}
                query = f"{' and '.join(self.metals)} battery metal price forecast market trend"
                try:
                    resp = requests.post('https://google.serper.dev/search', 
                                         headers=headers, 
                                         json={"q": query, "tbm": "nws"}, 
                                         timeout=10)
                    if resp.ok:
                        for article in resp.json().get('news', [])[:5]:
                            all_articles.append({
                                "metal": self.metals[0],
                                "headline": article.get("title", ""),
                                "source": article.get("source", "Serper Fallback"),
                                "sentiment": "neutral",
                                "price_mentions": []
                            })
                except Exception as e:
                    logger.error(f"Serper fallback failed: {e}")

        # Compute sentiment per metal
        sentiment = self._compute_sentiment(all_articles)

        # Extract all concrete price mentions
        price_mentions = []
        for article in all_articles:
            for mention in article.get("price_mentions", []):
                price_mentions.append({
                    **mention,
                    "metal": article["metal"],
                    "source": article["source"],
                    "headline": article["headline"],
                })

        # Build top headlines (de-duplicated, sorted by relevance)
        top_headlines = self._rank_headlines(all_articles)

        # Format for agent
        recommendation_inputs = self._format_for_agent(
            sentiment, price_mentions, lme_inventory, top_headlines
        )

        return {
            "articles_total": len(all_articles),
            "articles": all_articles,
            "sentiment_summary": sentiment,
            "price_mentions": price_mentions,
            "lme_inventory": lme_inventory,
            "top_headlines": top_headlines,
            "recommendation_inputs": recommendation_inputs,
            "timestamp": datetime.utcnow().isoformat(),
        }

    def _compute_sentiment(self, articles: list) -> dict:
        """Count bullish/bearish/neutral articles per metal."""
        summary = {metal: {"bullish": 0, "bearish": 0, "neutral": 0} for metal in self.metals}

        for article in articles:
            metal = article.get("metal")
            sentiment = article.get("sentiment", "neutral")
            if metal in summary:
                summary[metal][sentiment] = summary[metal].get(sentiment, 0) + 1

        # Add overall signal
        for metal in self.metals:
            bull = summary[metal]["bullish"]
            bear = summary[metal]["bearish"]
            total = bull + bear + summary[metal]["neutral"]
            if total == 0:
                summary[metal]["signal"] = "NO DATA"
                summary[metal]["total_articles"] = 0
                continue

            summary[metal]["total_articles"] = total
            bull_ratio = bull / total

            if bull_ratio >= 0.60:
                summary[metal]["signal"] = "BULLISH"
            elif bull_ratio <= 0.35:
                summary[metal]["signal"] = "BEARISH"
            else:
                summary[metal]["signal"] = "MIXED"

        return summary

    def _rank_headlines(self, articles: list) -> list:
        """Return top 10 unique headlines, prioritizing those with price mentions."""
        seen = set()
        ranked = []

        # First: articles with concrete price mentions
        for a in articles:
            h = a.get("headline", "")
            if h and h not in seen and a.get("price_mentions"):
                seen.add(h)
                ranked.append({"headline": h, "source": a["source"], "metal": a["metal"],
                               "sentiment": a.get("sentiment", "neutral"),
                               "has_price": True})

        # Then: remaining articles
        for a in articles:
            h = a.get("headline", "")
            if h and h not in seen:
                seen.add(h)
                ranked.append({"headline": h, "source": a["source"], "metal": a["metal"],
                               "sentiment": a.get("sentiment", "neutral"),
                               "has_price": False})

        return ranked[:10]

    def _format_for_agent(
        self,
        sentiment: dict,
        price_mentions: list,
        lme_inventory: dict,
        top_headlines: list,
    ) -> str:
        """
        Builds a pre-formatted context block for the LLM forecaster agent.
        This replaces raw web search with structured, parsed intelligence.
        """
        lines = [
            "═══ MARKET INTELLIGENCE REPORT ═══",
            f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            "── SENTIMENT BY METAL ──",
        ]

        for metal, data in sentiment.items():
            total = data.get("total_articles", 0)
            if total == 0:
                lines.append(f"  {metal.upper()}: No articles found")
                continue
            lines.append(
                f"  {metal.upper()}: {data['signal']} "
                f"({data['bullish']} bullish / {data['bearish']} bearish / "
                f"{data['neutral']} neutral | {total} articles)"
            )

        lines += ["", "── PRICE MENTIONS IN ARTICLES ──"]
        if price_mentions:
            for pm in price_mentions[:8]:
                lines.append(
                    f"  {pm['metal'].upper()}: ${pm['value']:,.0f}/tonne — "
                    f"[{pm['source']}] \"{pm['headline'][:60]}...\""
                )
        else:
            lines.append("  No concrete price figures found in scraped articles.")

        lines += ["", "── LME INVENTORY SIGNALS ──"]
        if lme_inventory:
            for metal, data in lme_inventory.items():
                lines.append(
                    f"  {metal.upper()}: {data['lme_stock_tonnes']:,.0f} tonnes → {data['signal']}"
                )
        else:
            lines.append("  LME inventory data unavailable.")

        lines += ["", "── TOP HEADLINES ──"]
        for i, h in enumerate(top_headlines, 1):
            tag = "[HAS PRICE]" if h["has_price"] else ""
            lines.append(f"  {i}. [{h['metal'].upper()}] {h['headline']} {tag}")
            lines.append(f"     Source: {h['source']} | Sentiment: {h['sentiment']}")

        lines += [
            "",
            "═══ END OF MARKET INTELLIGENCE ═══",
            "",
            "Based on the above data, provide your SELL TODAY or HOLD INVENTORY recommendation.",
        ]

        return "\n".join(lines)


def get_market_intelligence(metals: list[str]) -> dict:
    """Public API for the news aggregator."""
    return NewsAggregator(metals=metals).fetch_all()
