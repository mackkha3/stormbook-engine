"""Database setup. SQLite locally, Postgres on Railway; the URL comes from DATABASE_URL."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from booking_engine.models import Base

# Columns added to tables that already exist in production. create_all() only makes
# missing tables, so these are added by migrate(): additive only, never a drop, a
# rename or a type change, and safe to run any number of times.
ADDED_COLUMNS: dict[str, tuple[str, ...]] = {
    "conversations": ("address", "given_name", "address_requested_at", "address_missing"),
}

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
    """Create tables if needed, add any missing columns, and return the session factory."""
    Base.metadata.create_all(engine)
    migrate(engine)
    return sessionmaker(engine, expire_on_commit=False)


def migrate(engine: Engine) -> list[str]:
    """ADD COLUMN for each ADDED_COLUMNS entry the table lacks. Returns what was added.

    Each column's type comes from the model; a NOT NULL column carries its server
    default so existing rows get a value. Postgres also gets IF NOT EXISTS, so two
    processes starting at once cannot trip over each other.
    """
    added: list[str] = []
    if_not_exists = "IF NOT EXISTS " if engine.dialect.name == "postgresql" else ""
    for table_name, names in ADDED_COLUMNS.items():
        table = Base.metadata.tables[table_name]
        existing = {c["name"] for c in inspect(engine).get_columns(table_name)}
        for name in names:
            if name in existing:
                continue
            column = table.c[name]
            ddl = column.type.compile(dialect=engine.dialect)
            if not column.nullable:
                default = column.server_default.arg.compile(dialect=engine.dialect)
                ddl += f" NOT NULL DEFAULT {default}"
            with engine.begin() as conn:
                conn.execute(text(
                    f"ALTER TABLE {table_name} ADD COLUMN {if_not_exists}{name} {ddl}"
                ))
            added.append(f"{table_name}.{name}")
    return added
