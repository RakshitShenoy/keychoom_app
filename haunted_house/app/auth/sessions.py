"""Session cookies and token -> identity resolution."""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.tokens import hash_token
from app.db.models import Game, Player
from app.timers.clock import utcnow

if TYPE_CHECKING:
    from app.config import Settings


def set_session_cookie(
    response: Response, settings: "Settings", name: str, token: str, max_age_seconds: int,
    request_is_https: bool = False,
) -> None:
    """``Secure`` is set when COOKIE_SECURE=true OR the request really arrived over HTTPS. Behind a
    proxy (Replit) uvicorn's --proxy-headers makes the scheme follow X-Forwarded-Proto, so a deployment
    can never silently hand out non-Secure session cookies just because the variable was forgotten."""
    response.set_cookie(
        key=name,
        value=token,
        max_age=max_age_seconds,
        httponly=True,  # not readable from JavaScript
        secure=settings.cookie_secure or request_is_https,
        samesite=settings.cookie_samesite,
        path="/",
    )


def resolve_host_game(db: Session, settings: "Settings", token: str | None) -> Game | None:
    if not token:
        return None
    game = db.scalar(select(Game).where(Game.host_token_hash == hash_token(token, settings.secret_key)))
    if game is None or game.host_session_expires_at <= utcnow():
        return None
    return game


def resolve_player(db: Session, settings: "Settings", token: str | None) -> Player | None:
    if not token:
        return None
    player = db.scalar(
        select(Player).where(Player.session_token_hash == hash_token(token, settings.secret_key))
    )
    if player is None or player.session_expires_at <= utcnow():
        return None
    return player
