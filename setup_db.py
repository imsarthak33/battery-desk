#!/usr/bin/env python3
"""
setup_db.py
────────────
Initialize the database tables for BatteryDesk.
This script creates all required tables if they don't exist.
"""

import sys
import os

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from Database.connection import init_db

if __name__ == "__main__":
    print("Initializing BatteryDesk database...")
    try:
        init_db()
        print("✅ Database tables created successfully!")
    except Exception as e:
        print(f"❌ Error initializing database: {e}")
        sys.exit(1)