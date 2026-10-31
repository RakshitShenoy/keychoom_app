"""Declarative base, constraint naming convention and engine factory."""
from __future__ import annotations

from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase

# Deterministic constraint names keep Alembic migrations portable (SQLite <-> PostgreSQL).
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def make_engine(
    url: str, pool_size: int = 50, max_overflow: int = 50, pool_timeout: float = 30
) -> Engine:
    """Connection pool sized for the web server's worker threads.

    SQLAlchemy's default (5 + 10 connections) is smaller than the 40 threads FastAPI uses for
    synchronous endpoints, so a burst of players made requests wait for a connection until they
    timed out with HTTP 500 (a 502 behind a proxy). The pool must be at least as large as the thread pool.
    """
    is_sqlite = url.startswith("sqlite")
    connect_args = {"check_same_thread": False, "timeout": 30} if is_sqlite else {}
    pool_args = {} if ":memory:" in url else {
        "pool_size": pool_size, "max_overflow": max_overflow, "pool_timeout": pool_timeout}
    engine = create_engine(url, connect_args=connect_args, pool_pre_ping=not is_sqlite, **pool_args)

    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - trivial
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")  # SQLite ignores FKs unless asked
            cursor.execute("PRAGMA journal_mode=WAL")  # readers don't block the writer
            cursor.close()

    return engine
