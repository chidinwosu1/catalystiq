"""SQLAlchemy engine/session setup.

Defaults to a local SQLite file so the app and tests run without any
infrastructure. Point DATABASE_URL at Postgres in production, per the
target architecture (build spec §1.1 "Processed data store (PostgreSQL)").
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from catalystiq.config import get_settings


class Base(DeclarativeBase):
    pass


def normalize_database_url(url: str) -> str:
    """Render (and some other hosts) hand out a `postgres://` URL, but
    SQLAlchemy 2.x only recognizes the `postgresql://` scheme (with the
    psycopg2 driver). Normalize it so the app and Alembic both connect."""
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


def make_engine(database_url: str | None = None):
    url = normalize_database_url(database_url or get_settings().database_url)
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args, future=True)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
