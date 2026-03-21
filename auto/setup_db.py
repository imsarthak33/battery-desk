"""
setup_db.py
────────────
Run this ONCE to create all database tables.
Also shows current table stats if run again.

Usage:
  python setup_db.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from database.connection import init_db, engine
from database.models import (
    MetalPrice, ForexRate, MarginAnalysis, NewsArticle,
    SentimentScore, LMEInventory, Forecast, PricePrediction
)
from sqlalchemy import inspect, text
from sqlalchemy.orm import sessionmaker

def main():
    print("\n🔋 BatteryDesk Database Setup")
    print("=" * 40)

    # Create tables
    print("\n📦 Creating tables...")
    init_db()
    print("✅ All tables created")

    # Show table info
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    print(f"\n📋 Tables ({len(tables)}):")
    for t in sorted(tables):
        print(f"   · {t}")

    # Show row counts
    Session = sessionmaker(bind=engine)
    db = Session()
    print("\n📊 Current row counts:")
    models = [
        ("metal_prices", MetalPrice),
        ("forex_rates", ForexRate),
        ("margin_analyses", MarginAnalysis),
        ("news_articles", NewsArticle),
        ("sentiment_scores", SentimentScore),
        ("lme_inventory", LMEInventory),
        ("forecasts", Forecast),
        ("price_predictions", PricePrediction),
    ]
    for name, model in models:
        count = db.query(model).count()
        bar = "█" * min(count, 30) + "░" * max(0, 10 - min(count, 10))
        print(f"   {name:<22} {count:>6} rows  {bar}")

    db.close()

    db_type = "PostgreSQL" if os.getenv("DATABASE_URL") else "SQLite (batterydesk.db)"
    print(f"\n💾 Database: {db_type}")
    print("\n✅ Setup complete! Run: python -m uvicorn app:app --reload")
    print()

if __name__ == "__main__":
    main()
