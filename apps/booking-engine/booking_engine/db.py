"""Database setup. SQLite locally, Postgres on Railway; the URL comes from DATABASE_URL."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from booking_engine.models import Base

_POSTGRES_PREFIXES = ("postgres://", "postgresql://")
_POSTGRES_DRIVER = "postgresql+psycopg://"


def normalize_url(database_url: str) -> str:
    """Point a plain Postgres URL (what Railway hands out) at the installed psycopg driver."""
    for prefix in _POSTGRES_PREFIXES:
        if database_url.startswith(prefix):
            return _POSTGRES_DRIVER + database_url[len(prefix):]
    return database_url


def make_engine(database_url: str) -> Engine:
    database_url = normalize_url(database_url)
    if database_url.startswith("sqlite"):
        kwargs: dict = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in database_url or database_url in ("sqlite://", "sqlite:///"):
            kwargs["poolclass"] = StaticPool   # one shared connection, or the tables vanish
        return create_engine(database_url, **kwargs)
    return create_engine(database_url, pool_pre_ping=True)


def init_db(engine: Engine) -> sessionmaker[Session]:
    """Create tables if needed and return the session factory."""
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)
