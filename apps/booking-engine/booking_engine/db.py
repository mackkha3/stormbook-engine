"""Database setup. SQLite locally; the URL comes from DATABASE_URL so it can move to Postgres."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from booking_engine.models import Base


def make_engine(database_url: str) -> Engine:
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
