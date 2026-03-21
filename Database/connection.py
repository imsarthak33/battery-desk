"""
database/connection.py
───────────────────────
Database connection setup.
Uses SQLite locally and PostgreSQL on Railway (auto-detected via DATABASE_URL env var).
"""

import os
import logging
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from contextlib import contextmanager

from .models import Base

logger = logging.getLogger(__name__)

# ── Detect database URL ────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "")

if DATABASE_URL:
    # Railway / Production PostgreSQL
    # Railway gives postgres:// but SQLAlchemy needs postgresql://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,      # reconnect if connection dropped
        pool_size=5,
        max_overflow=10,
        echo=False,
    )
    logger.info("Connected to PostgreSQL (Railway)")
else:
    # Local SQLite — zero config, works immediately
    DB_PATH = os.path.join(os.path.dirname(__file__), "..", "batterydesk.db")
    DB_PATH = os.path.abspath(DB_PATH)
    engine = create_engine(
        f"sqlite:///{DB_PATH}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    # Enable WAL mode for better concurrent reads on SQLite
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    logger.info(f"Connected to SQLite: {DB_PATH}")

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """Create all tables if they don't exist."""
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables initialized")


@contextmanager
def get_db() -> Session:
    """Context manager for database sessions with auto-commit/rollback."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"DB session error: {e}")
        raise
    finally:
        db.close()
