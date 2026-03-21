"""
database/connection.py — Fixed
SQLite locally, PostgreSQL on Railway (auto via DATABASE_URL).
"""

import os
import logging
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Import Base — must be after logger to avoid circular issues
from database.models import Base

DATABASE_URL = os.getenv("DATABASE_URL", "")

if DATABASE_URL:
    # Railway PostgreSQL — fix URL prefix
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        echo=False,
    )
    logger.info("Connected to PostgreSQL (Railway)")
else:
    # Local SQLite
    DB_PATH = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "batterydesk.db")
    )
    engine = create_engine(
        f"sqlite:///{DB_PATH}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    logger.info(f"Connected to SQLite: {DB_PATH}")

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """Create all tables."""
    Base.metadata.create_all(bind=engine)
    logger.info("All database tables created successfully")


@contextmanager
def get_db():
    """
    Context manager for DB sessions.
    Always call inside: with get_db() as db:
    """
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