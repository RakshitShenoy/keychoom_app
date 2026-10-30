from __future__ import annotations

from typing import AsyncIterator

from fastapi import Request
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import make_engine


class Database:
    """Owns the engine and session factory for one application instance."""

    def __init__(self, url: str, **pool_options) -> None:
        self.engine: Engine = make_engine(url, **pool_options)
        self.session_factory = sessionmaker(
            bind=self.engine, expire_on_commit=False, class_=Session
        )

    def dispose(self) -> None:
        self.engine.dispose()


async def get_db(request: Request) -> AsyncIterator[Session]:
    """FastAPI dependency: one session per request.

    This is deliberately an ``async`` generator. A plain (sync) generator dependency is closed in the
    shared 40-thread worker pool, so under load the cleanup of FINISHED requests waited in the same
    queue as everything else and their database connections stayed checked out until the pool ran
    dry (HTTP 500 / 502 for the players). Creating a Session is lazy (no connection yet) and closing
    one only hands its connection back to the pool, so doing both on the event loop is cheap.
    """
    session = request.app.state.db.session_factory()
    try:
        yield session
    finally:
        session.close()
