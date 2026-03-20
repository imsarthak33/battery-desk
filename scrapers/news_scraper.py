"""
scrapers/news_scraper.py
─────────────────────────
Scrapes metal price news and signals from multiple sources:
  - Reuters (commodities section)
  - Mining.com
  - Metal Bulletin (free articles)
  - Business Standard (India-specific battery/metals coverage)
  - Economic Times Commodities
  - LME Notices (public)

Extracts:
  1. Price mentions from articles (e.g., "Nickel fell to $16,200/tonne")
  2. Directional signals (bearish/bullish keywords)
  3. Article headlines with timestamps

This feeds Agent 4 (Forecaster) with structured data instead of raw search snippets.
"""

import re
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper, human_delay


# ── Signal keyword sets ────────────────────────────────────────────────────────

BEARISH_KEYWORDS = {
    "fell", "fallen", "drop", "dropped", "decline", "declined", "slump", "slumped",
    "tumble", "tumbled", "crash", "plunge", "plunged", "down", "lower", "weakness",
    "oversupply", "surplus", "bearish", "sell-off", "selloff", "pressure", "retreat",
    "correction", "headwind", "slowdown", "demand concern", "inventory build",
}

BULLISH_KEYWORDS = {
    "rose", "risen", "surge", "surged", "rally", "rallied", "gain", "gained",
    "climb", "climbed", "jump", "jumped", "up", "higher", "strength", "bullish",
    "deficit", "shortage", "supply crunch", "demand growth", "positive", "upside",
    "recovery", "rebound", "tight supply", "underinvestment", "strike",
}


class ReutersMetalsScraper(BaseScraper):
    """Scrapes Reuters commodities section for metal news."""

    SOURCE_NAME = "reuters.com"
    BASE_URL = "https://www.reuters.com"

    SEARCH_URLS = {
        "nickel":  "https://www.reuters.com/markets/commodities/nickel/",
        "cobalt":  "https://www.reuters.com/search/news?blob=cobalt+price",
        "lithium": "https://www.reuters.com/markets/commodities/lithium/",
        "lead":    "https://www.reuters.com/search/news?blob=LME+lead+price",
    }

    def __init__(self, metals: list[str] = None):
        super().__init__(timeout=15)
        self.metals = metals or ["nickel", "cobalt", "lithium"]

    def scrape(self) -> dict:
        all_articles = []

        for metal in self.metals:
            url = self.SEARCH_URLS.get(metal, f"https://www.reuters.com/search/news?blob={metal}+price")
            articles = self._scrape_section(url, metal)
            all_articles.extend(articles)
            human_delay(2.0, 4.0)

        return {
            "source": self.SOURCE_NAME,
            "articles": all_articles,
            "count": len(all_articles),
            "timestamp": datetime.utcnow().isoformat(),
        }

    def _scrape_section(self, url: str, metal: str) -> list:
        resp = self.get(url, extra_headers={"Referer": "https://www.google.com"})
        if not resp:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        articles = []

        # Reuters article structure: <article> or [data-testid="MediaStoryCard"]
        story_els = (
            soup.find_all("article") or
            soup.find_all(attrs={"data-testid": re.compile(r"[Ss]tory|[Cc]ard|[Aa]rticle")})
        )

        for el in story_els[:8]:  # Top 8 articles
            headline_el = (
                el.find("h3") or el.find("h2") or
                el.find(attrs={"data-testid": re.compile(r"[Hh]eading|[Tt]itle")})
            )
            if not headline_el:
                continue

            headline = headline_el.get_text(strip=True)
            if not headline or len(headline) < 10:
                continue

            # Check metal relevance
            if metal.lower() not in headline.lower() and "metal" not in headline.lower():
                continue

            # Extract price mentions
            price_mentions = self._extract_price_mentions(headline, metal)

            # Detect sentiment
            sentiment = self._detect_sentiment(headline)

            # Get publication date if available
            time_el = el.find("time")
            pub_date = time_el.get("datetime", "") if time_el else ""

            articles.append({
                "metal": metal,
                "headline": headline,
                "source": self.SOURCE_NAME,
                "url": self.BASE_URL + (el.find("a", href=True) or {}).get("href", ""),
                "price_mentions": price_mentions,
                "sentiment": sentiment,
                "published": pub_date,
            })

        return articles

    def _extract_price_mentions(self, text: str, metal: str) -> list:
        """Extract price figures like '$16,450/tonne' or '16450 USD per tonne'"""
        patterns = [
            r"\$\s*([\d,]+\.?\d*)\s*(?:per\s+)?(?:tonne|ton|mt|lb|kg)",
            r"([\d,]+\.?\d*)\s*(?:USD|US\$|dollars?)\s*(?:per\s+)?(?:tonne|ton|mt)",
            r"([\d,]+)\s*(?:a|per)\s+(?:tonne|ton|mt)",
        ]
        mentions = []
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                val = self.safe_float(match.group(1))
                if val and val > 100:
                    mentions.append({"value": val, "unit": "USD/tonne", "context": match.group(0)})
        return mentions

    def _detect_sentiment(self, text: str) -> str:
        """Returns 'bullish', 'bearish', or 'neutral'"""
        text_lower = text.lower()
        bull_score = sum(1 for kw in BULLISH_KEYWORDS if kw in text_lower)
        bear_score = sum(1 for kw in BEARISH_KEYWORDS if kw in text_lower)
        if bull_score > bear_score:
            return "bullish"
        elif bear_score > bull_score:
            return "bearish"
        return "neutral"


class MiningDotComScraper(BaseScraper):
    """
    mining.com — excellent free source for battery metals news and prices.
    Covers lithium and cobalt extensively.
    """

    SOURCE_NAME = "mining.com"
    BASE_URL = "https://www.mining.com"

    CATEGORY_URLS = {
        "nickel":  "https://www.mining.com/metal/nickel/",
        "cobalt":  "https://www.mining.com/metal/cobalt/",
        "lithium": "https://www.mining.com/metal/lithium/",
        "lead":    "https://www.mining.com/metal/lead/",
    }

    def __init__(self, metals: list[str] = None):
        super().__init__(timeout=15)
        self.metals = metals or ["nickel", "cobalt", "lithium"]

    def scrape(self) -> dict:
        """
        mining.com has a public price ticker AND news articles.
        We extract both.
        """
        prices = {}
        articles = []

        for metal in self.metals:
            url = self.CATEGORY_URLS.get(metal)
            if not url:
                continue

            metal_data = self._scrape_metal_page(url, metal)
            if metal_data.get("price"):
                prices[metal] = metal_data["price"]
            articles.extend(metal_data.get("articles", []))
            human_delay(1.5, 3.0)

        return {
            "source": self.SOURCE_NAME,
            "prices": prices,   # {metal: {"price": X, "unit": "USD/tonne"}}
            "articles": articles,
            "timestamp": datetime.utcnow().isoformat(),
        }

    def _scrape_metal_page(self, url: str, metal: str) -> dict:
        resp = self.get(url)
        if not resp:
            return {}

        soup = BeautifulSoup(resp.text, "html.parser")
        result = {"articles": []}

        # mining.com shows a price box at top of metal category pages
        # Look for price in common locations
        price_box = (
            soup.find(class_=re.compile(r"price|spot|current", re.I)) or
            soup.find(attrs={"data-price": True})
        )
        if price_box:
            val = self.safe_float(price_box.get("data-price") or price_box.get_text(strip=True))
            if val and val > 100:
                result["price"] = {"price": val, "unit": "USD/tonne", "source": self.SOURCE_NAME}

        # Extract article headlines
        article_els = soup.find_all("article") or soup.find_all("h2", class_=re.compile(r"title|heading"))
        for el in article_els[:6]:
            headline = el.find("h2") or el.find("h3") or el
            text = headline.get_text(strip=True) if headline else ""
            if len(text) > 15:
                result["articles"].append({
                    "metal": metal,
                    "headline": text,
                    "source": self.SOURCE_NAME,
                    "sentiment": self._detect_sentiment(text),
                    "price_mentions": self._extract_price_mentions(text),
                })

        return result

    def _extract_price_mentions(self, text: str) -> list:
        matches = re.findall(r"\$?([\d,]+\.?\d*)\s*(?:per\s+)?(?:tonne|ton|lb|kg)", text, re.I)
        result = []
        for m in matches:
            val = self.safe_float(m)
            if val and val > 100:
                result.append({"value": val, "unit": "USD/tonne"})
        return result

    def _detect_sentiment(self, text: str) -> str:
        text_lower = text.lower()
        bull = sum(1 for kw in BULLISH_KEYWORDS if kw in text_lower)
        bear = sum(1 for kw in BEARISH_KEYWORDS if kw in text_lower)
        if bull > bear:
            return "bullish"
        elif bear > bull:
            return "bearish"
        return "neutral"


class EconomicTimesScraper(BaseScraper):
    """
    Economic Times Commodities — India-specific coverage.
    Good for MCX context, Indian demand signals, GST/policy news.
    """

    SOURCE_NAME = "economictimes.indiatimes.com"
    BASE_URL = "https://economictimes.indiatimes.com"

    SEARCH_URLS = {
        "nickel":  "https://economictimes.indiatimes.com/topic/nickel-prices",
        "cobalt":  "https://economictimes.indiatimes.com/topic/cobalt",
        "lithium": "https://economictimes.indiatimes.com/topic/lithium",
        "lead":    "https://economictimes.indiatimes.com/topic/lead-prices",
    }

    def __init__(self, metals: list[str] = None):
        super().__init__(timeout=15)
        self.metals = metals or ["nickel", "cobalt", "lithium", "lead"]

    def scrape(self) -> dict:
        articles = []
        for metal in self.metals:
            url = self.SEARCH_URLS.get(metal, f"{self.BASE_URL}/topic/{metal}")
            new_articles = self._scrape_topic_page(url, metal)
            articles.extend(new_articles)
            human_delay(1.5, 2.5)

        return {
            "source": self.SOURCE_NAME,
            "articles": articles,
            "count": len(articles),
            "timestamp": datetime.utcnow().isoformat(),
        }

    def _scrape_topic_page(self, url: str, metal: str) -> list:
        resp = self.get(url, extra_headers={"Referer": "https://www.google.co.in"})
        if not resp:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        articles = []

        # ET uses <div class="eachStory"> or <li class="clearfix">
        story_els = (
            soup.find_all("div", class_="eachStory") or
            soup.find_all("li", class_=re.compile(r"story|article|news")) or
            soup.find_all("h3")
        )

        for el in story_els[:8]:
            headline_el = el.find("h3") or el.find("h2") or (el if el.name in ["h3", "h2"] else None)
            if not headline_el:
                continue
            text = headline_el.get_text(strip=True)
            if len(text) < 10:
                continue

            articles.append({
                "metal": metal,
                "headline": text,
                "source": self.SOURCE_NAME,
                "sentiment": self._detect_sentiment(text),
            })

        return articles

    def _detect_sentiment(self, text: str) -> str:
        text_lower = text.lower()
        bull = sum(1 for kw in BULLISH_KEYWORDS if kw in text_lower)
        bear = sum(1 for kw in BEARISH_KEYWORDS if kw in text_lower)
        if bull > bear: return "bullish"
        if bear > bull: return "bearish"
        return "neutral"


class LMENoticesScraper(BaseScraper):
    """
    Scrapes LME public notices and stock reports.
    LME publishes daily stock reports and notices publicly — no subscription needed.
    These contain inventory levels (key supply signal).
    """

    SOURCE_NAME = "lme.com (public)"
    BASE_URL = "https://www.lme.com"

    def __init__(self):
        super().__init__(timeout=20)

    def scrape(self) -> dict:
        """Fetch LME daily stock report (publicly available)."""
        # LME publishes daily stock data as HTML tables
        stock_url = "https://www.lme.com/Market-Data/Reports-and-data/Stock-reports"
        resp = self.get(stock_url)
        if not resp:
            return {"source": self.SOURCE_NAME, "data": {}, "success": False}

        soup = BeautifulSoup(resp.text, "html.parser")
        stocks = {}

        metal_targets = {
            "Nickel": "nickel",
            "Cobalt": "cobalt",
            "Lead": "lead",
            "Aluminium": "aluminum",
        }

        # LME stock tables have metal names in first column, stock in tonnes
        tables = soup.find_all("table")
        for table in tables:
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all(["td", "th"])
                if len(cells) < 2:
                    continue
                first_cell = cells[0].get_text(strip=True)
                for lme_name, our_key in metal_targets.items():
                    if lme_name.lower() in first_cell.lower():
                        # Look for stock figure in remaining cells
                        for cell in cells[1:]:
                            val = self.safe_float(cell.get_text(strip=True))
                            if val and val > 0:
                                stocks[our_key] = {
                                    "lme_stock_tonnes": val,
                                    "signal": "high inventory (bearish)" if val > 100000 else "low inventory (bullish)",
                                }
                                break

        return {
            "source": self.SOURCE_NAME,
            "stocks": stocks,
            "timestamp": datetime.utcnow().isoformat(),
            "success": len(stocks) > 0,
            "note": "LME warehouse stock — high stock = bearish (oversupply), low stock = bullish (tight)",
        }
