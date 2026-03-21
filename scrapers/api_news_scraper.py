"""
scrapers/api_news_scraper.py
─────────────────────────────
Fetches battery metals news from REAL free APIs.
No scraping, no bot detection, no fragile HTML parsing.

APIs Used:
  1. GNews          — gnews.io              (100 req/day free)
  2. MarketAux      — marketaux.com         (100 req/day free, has sentiment tags)
  3. NewsData.io    — newsdata.io           (200 req/day free)
  4. Currents API   — currentsapi.services  (600 req/day free)
  5. TheNews API    — thenewsapi.com        (100 req/day free)

All return: headline, URL, source, published date, sentiment
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── API Keys (add these to your .env file) ────────────────────────────────────
GNEWS_API_KEY       = os.getenv("GNEWS_API_KEY", "")
MARKETAUX_API_KEY   = os.getenv("MARKETAUX_API_KEY", "")
NEWSDATA_API_KEY    = os.getenv("NEWSDATA_API_KEY", "")
CURRENTS_API_KEY    = os.getenv("CURRENTS_API_KEY", "")
THENEWS_API_KEY     = os.getenv("THENEWS_API_KEY", "")

# ── Search queries per metal ───────────────────────────────────────────────────
METAL_QUERIES = {
    "nickel":    ["nickel price LME", "nickel battery supply", "nickel mining"],
    "cobalt":    ["cobalt price market", "cobalt battery shortage", "DRC cobalt"],
    "lithium":   ["lithium price carbonate", "lithium battery demand", "lithium supply"],
    "lead":      ["lead metal price", "lead acid battery scrap India", "LME lead"],
    "manganese": ["manganese price", "manganese ore market"],
}

# Battery chemistry specific queries
CHEMISTRY_QUERIES = {
    "NMC":       "NMC battery scrap price nickel cobalt",
    "LCO":       "LCO cobalt battery recycling price",
    "LFP":       "LFP lithium iron phosphate battery price",
    "LEAD_ACID": "lead acid battery scrap price India",
    "NCA":       "NCA battery nickel cobalt price",
}

# Bearish / bullish keywords for sentiment detection
BEARISH = {"fell","fall","drop","decline","slump","crash","plunge","down","lower",
           "oversupply","surplus","bearish","sell-off","pressure","retreat","weak"}
BULLISH = {"rose","rise","surge","rally","gain","climb","jump","up","higher",
           "deficit","shortage","bullish","tight supply","recovery","demand","strong"}


def detect_sentiment(text: str) -> str:
    words = text.lower().split()
    bull = sum(1 for w in words if w.strip(".,!?;:") in BULLISH)
    bear = sum(1 for w in words if w.strip(".,!?;:") in BEARISH)
    if bull > bear: return "bullish"
    if bear > bull: return "bearish"
    return "neutral"


def extract_price_mentions(text: str) -> list:
    import re
    patterns = [
        r"\$\s*([\d,]+\.?\d*)\s*(?:per\s+)?(?:tonne|ton|mt|lb|kg)",
        r"([\d,]+\.?\d*)\s*(?:USD|US\$)\s*(?:per\s+)?(?:tonne|ton)",
    ]
    mentions = []
    for p in patterns:
        for m in re.finditer(p, text, re.IGNORECASE):
            try:
                val = float(m.group(1).replace(",", ""))
                if val > 100:
                    mentions.append({"value": val, "unit": "USD/tonne", "context": m.group(0)[:40]})
            except ValueError:
                pass
    return mentions[:3]


# ══════════════════════════════════════════════════════════════════════════════
#  1. GNEWS API
# ══════════════════════════════════════════════════════════════════════════════

def fetch_gnews(metal: str, max_results: int = 5) -> list[dict]:
    """
    GNews: 100 req/day free. Returns articles with title, URL, source, date.
    Docs: https://gnews.io/docs/v4
    """
    if not GNEWS_API_KEY:
        logger.debug("GNEWS_API_KEY not set — skipping GNews")
        return []

    articles = []
    query = METAL_QUERIES.get(metal, [f"{metal} price"])[0]

    try:
        resp = requests.get(
            "https://gnews.io/api/v4/search",
            params={
                "q": query,
                "lang": "en",
                "country": "any",
                "max": max_results,
                "apikey": GNEWS_API_KEY,
                "sortby": "publishedAt",
            },
            timeout=10,
        )
        data = resp.json()

        for item in data.get("articles", []):
            headline = item.get("title", "")
            desc = item.get("description", "")
            full_text = headline + " " + desc

            articles.append({
                "metal": metal,
                "headline": headline,
                "description": desc,
                "url": item.get("url", ""),
                "source": item.get("source", {}).get("name", "GNews"),
                "published": item.get("publishedAt", ""),
                "sentiment": detect_sentiment(full_text),
                "price_mentions": extract_price_mentions(full_text),
                "api": "gnews",
            })

        logger.info(f"GNews: {len(articles)} articles for {metal}")
    except Exception as e:
        logger.warning(f"GNews error for {metal}: {e}")

    return articles


# ══════════════════════════════════════════════════════════════════════════════
#  2. MARKETAUX API (Best for financial/commodity news — has built-in sentiment)
# ══════════════════════════════════════════════════════════════════════════════

def fetch_marketaux(metal: str, max_results: int = 5) -> list[dict]:
    """
    MarketAux: 100 req/day free. Financial news with sentiment + entity tagging.
    Docs: https://www.marketaux.com/documentation
    Best API for your use case — already detects positive/negative sentiment.
    """
    if not MARKETAUX_API_KEY:
        logger.debug("MARKETAUX_API_KEY not set — skipping MarketAux")
        return []

    articles = []
    query = METAL_QUERIES.get(metal, [f"{metal} commodity"])[0]

    try:
        resp = requests.get(
            "https://api.marketaux.com/v1/news/all",
            params={
                "search": query,
                "language": "en",
                "api_token": MARKETAUX_API_KEY,
                "limit": max_results,
                "sort": "published_at",
                "filter_entities": "true",
            },
            timeout=10,
        )
        data = resp.json()

        for item in data.get("data", []):
            headline = item.get("title", "")
            desc = item.get("description", "") or ""
            full_text = headline + " " + desc

            # MarketAux provides sentiment natively
            entities = item.get("entities", [])
            marketaux_sentiment = None
            if entities:
                # Average entity sentiments
                sentiments = [e.get("sentiment_score", 0) for e in entities if e.get("sentiment_score") is not None]
                if sentiments:
                    avg = sum(sentiments) / len(sentiments)
                    marketaux_sentiment = "bullish" if avg > 0.1 else "bearish" if avg < -0.1 else "neutral"

            articles.append({
                "metal": metal,
                "headline": headline,
                "description": desc[:200],
                "url": item.get("url", ""),
                "source": item.get("source", "MarketAux"),
                "published": item.get("published_at", ""),
                "sentiment": marketaux_sentiment or detect_sentiment(full_text),
                "price_mentions": extract_price_mentions(full_text),
                "api": "marketaux",
                "sentiment_score": sum([e.get("sentiment_score", 0) for e in entities]) / max(len(entities), 1),
            })

        logger.info(f"MarketAux: {len(articles)} articles for {metal}")
    except Exception as e:
        logger.warning(f"MarketAux error for {metal}: {e}")

    return articles


# ══════════════════════════════════════════════════════════════════════════════
#  3. NEWSDATA.IO
# ══════════════════════════════════════════════════════════════════════════════

def fetch_newsdata(metal: str, max_results: int = 5) -> list[dict]:
    """
    NewsData.io: 200 req/day free. Good global coverage.
    Docs: https://newsdata.io/documentation
    """
    if not NEWSDATA_API_KEY:
        logger.debug("NEWSDATA_API_KEY not set — skipping NewsData")
        return []

    articles = []
    query = METAL_QUERIES.get(metal, [f"{metal} price"])[0]

    try:
        resp = requests.get(
            "https://newsdata.io/api/1/news",
            params={
                "q": query,
                "language": "en",
                "category": "business,science",
                "apikey": NEWSDATA_API_KEY,
            },
            timeout=10,
        )
        data = resp.json()

        for item in (data.get("results") or [])[:max_results]:
            headline = item.get("title", "")
            desc = item.get("description", "") or ""
            full_text = headline + " " + desc

            articles.append({
                "metal": metal,
                "headline": headline,
                "description": desc[:200],
                "url": item.get("link", ""),
                "source": item.get("source_id", "NewsData"),
                "published": item.get("pubDate", ""),
                "sentiment": detect_sentiment(full_text),
                "price_mentions": extract_price_mentions(full_text),
                "api": "newsdata",
            })

        logger.info(f"NewsData: {len(articles)} articles for {metal}")
    except Exception as e:
        logger.warning(f"NewsData error for {metal}: {e}")

    return articles


# ══════════════════════════════════════════════════════════════════════════════
#  4. CURRENTS API (600 req/day free — most generous)
# ══════════════════════════════════════════════════════════════════════════════

def fetch_currents(metal: str, max_results: int = 5) -> list[dict]:
    """
    Currents API: 600 req/day free — most generous free tier.
    Docs: https://currentsapi.services/en/docs/
    """
    if not CURRENTS_API_KEY:
        logger.debug("CURRENTS_API_KEY not set — skipping Currents")
        return []

    articles = []
    query = METAL_QUERIES.get(metal, [f"{metal}"])[0]

    try:
        resp = requests.get(
            "https://api.currentsapi.services/v1/search",
            params={
                "keywords": query,
                "language": "en",
                "apiKey": CURRENTS_API_KEY,
            },
            timeout=10,
        )
        data = resp.json()

        for item in (data.get("news") or [])[:max_results]:
            headline = item.get("title", "")
            desc = item.get("description", "") or ""
            full_text = headline + " " + desc

            articles.append({
                "metal": metal,
                "headline": headline,
                "description": desc[:200],
                "url": item.get("url", ""),
                "source": item.get("author", "Currents"),
                "published": item.get("published", ""),
                "sentiment": detect_sentiment(full_text),
                "price_mentions": extract_price_mentions(full_text),
                "api": "currents",
            })

        logger.info(f"Currents: {len(articles)} articles for {metal}")
    except Exception as e:
        logger.warning(f"Currents error for {metal}: {e}")

    return articles


# ══════════════════════════════════════════════════════════════════════════════
#  5. THENEWS API
# ══════════════════════════════════════════════════════════════════════════════

def fetch_thenews(metal: str, max_results: int = 5) -> list[dict]:
    """
    TheNews API: 100 req/day free. Good aggregator.
    Docs: https://www.thenewsapi.com/documentation
    """
    if not THENEWS_API_KEY:
        logger.debug("THENEWS_API_KEY not set — skipping TheNews")
        return []

    articles = []
    query = METAL_QUERIES.get(metal, [f"{metal} price"])[0]

    try:
        resp = requests.get(
            "https://api.thenewsapi.com/v1/news/all",
            params={
                "search": query,
                "language": "en",
                "api_token": THENEWS_API_KEY,
                "limit": max_results,
                "sort": "published_at",
                "categories": "business,tech",
            },
            timeout=10,
        )
        data = resp.json()

        for item in data.get("data", []):
            headline = item.get("title", "")
            desc = item.get("description", "") or ""
            full_text = headline + " " + desc

            articles.append({
                "metal": metal,
                "headline": headline,
                "description": desc[:200],
                "url": item.get("url", ""),
                "source": item.get("source", "TheNews"),
                "published": item.get("published_at", ""),
                "sentiment": detect_sentiment(full_text),
                "price_mentions": extract_price_mentions(full_text),
                "api": "thenews",
            })

        logger.info(f"TheNews: {len(articles)} articles for {metal}")
    except Exception as e:
        logger.warning(f"TheNews error for {metal}: {e}")

    return articles


# ══════════════════════════════════════════════════════════════════════════════
#  AGGREGATOR — Runs all APIs in parallel, deduplicates
# ══════════════════════════════════════════════════════════════════════════════

def fetch_all_news(metals: list[str], chemistry: str = "") -> dict:
    """
    Main function — runs all available APIs for all metals.
    Returns structured dict matching what news_aggregator.py expects.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    fetchers = [fetch_gnews, fetch_marketaux, fetch_newsdata, fetch_currents, fetch_thenews]
    all_articles = []
    seen_urls = set()
    seen_headlines = set()

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {}
        for metal in metals:
            for fn in fetchers:
                futures[executor.submit(fn, metal, 5)] = (fn.__name__, metal)

        for future in as_completed(futures, timeout=20):
            fname, metal = futures[future]
            try:
                articles = future.result(timeout=15)
                for a in articles:
                    # Deduplicate by URL and headline
                    url = a.get("url", "")
                    headline = a.get("headline", "").lower().strip()
                    key = url or headline
                    if key and key not in seen_urls and headline not in seen_headlines:
                        seen_urls.add(key)
                        seen_headlines.add(headline)
                        all_articles.append(a)
            except Exception as e:
                logger.warning(f"{fname} failed for {metal}: {e}")

    # Compute sentiment per metal
    sentiment_summary = {}
    for metal in metals:
        metal_arts = [a for a in all_articles if a.get("metal") == metal]
        bull = sum(1 for a in metal_arts if a["sentiment"] == "bullish")
        bear = sum(1 for a in metal_arts if a["sentiment"] == "bearish")
        neut = sum(1 for a in metal_arts if a["sentiment"] == "neutral")
        total = len(metal_arts)
        signal = "NO DATA"
        if total > 0:
            ratio = bull / total
            signal = "BULLISH" if ratio >= 0.6 else "BEARISH" if ratio <= 0.35 else "MIXED"
        sentiment_summary[metal] = {
            "bullish": bull, "bearish": bear, "neutral": neut,
            "total_articles": total, "signal": signal,
        }

    # Top headlines (prioritize articles with URLs and price mentions)
    ranked = sorted(all_articles, key=lambda a: (
        bool(a.get("url")),
        bool(a.get("price_mentions")),
        a.get("sentiment") != "neutral",
    ), reverse=True)

    top_headlines = [{
        "headline": a["headline"],
        "source": a["source"],
        "metal": a["metal"],
        "sentiment": a["sentiment"],
        "has_price": bool(a.get("price_mentions")),
        "url": a.get("url", ""),
    } for a in ranked[:12]]

    price_mentions = []
    for a in all_articles:
        for pm in a.get("price_mentions", []):
            price_mentions.append({**pm, "metal": a["metal"], "source": a["source"], "headline": a["headline"][:60]})

    # Which APIs actually returned data
    active_apis = list(set(a["api"] for a in all_articles))

    logger.info(f"API News: {len(all_articles)} total articles from {active_apis}")

    return {
        "articles": all_articles,
        "articles_total": len(all_articles),
        "sentiment_summary": sentiment_summary,
        "top_headlines": top_headlines,
        "price_mentions": price_mentions[:8],
        "lme_inventory": {},
        "active_apis": active_apis,
        "recommendation_inputs": f"Fetched {len(all_articles)} articles from {', '.join(active_apis)} covering {', '.join(metals)}",
    }
