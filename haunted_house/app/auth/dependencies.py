from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.auth.sessions import resolve_host_game, resolve_player
from app.db.models import Game, Player
from app.db.session import get_db
from app.errors import AppError


def _release_connection(db: Session) -> None:
    """End the read-only transaction opened by the session lookup so the database connection goes
    back to the pool while the request waits for its worker thread. Sessions are configured with
    expire_on_commit=False, so the loaded Game/Player objects stay usable and later queries simply
    begin a new transaction."""
    db.commit()


def require_host(request: Request, db: Session = Depends(get_db)) -> Game:
    settings = request.app.state.settings
    game = resolve_host_game(db, settings, request.cookies.get(settings.host_cookie_name))
    if game is None:
        raise AppError(401, "HOST_SESSION_REQUIRED", "A valid host session is required.")
    _release_connection(db)
    return game


def require_player(request: Request, db: Session = Depends(get_db)) -> Player:
    settings = request.app.state.settings
    player = resolve_player(db, settings, request.cookies.get(settings.player_cookie_name))
    if player is None:
        raise AppError(401, "PLAYER_SESSION_REQUIRED", "A valid player session is required.")
    _release_connection(db)
    return player


# --- per-game guards (Phase 2) ------------------------------------------------------------
# The game code in the URL is only a *claim*. It is always compared with the game the
# authenticated session actually belongs to; ids sent by the browser are never trusted.
from dataclasses import dataclass  # noqa: E402

from app.lobby.codes import normalize_game_code  # noqa: E402


def require_host_of_game(
    game_code: str, request: Request, db: Session = Depends(get_db)
) -> Game:
    settings = request.app.state.settings
    code = normalize_game_code(game_code)
    host_game = resolve_host_game(db, settings, request.cookies.get(settings.host_cookie_name))
    if host_game is None:
        if resolve_player(db, settings, request.cookies.get(settings.player_cookie_name)):
            raise AppError(403, "HOST_ONLY", "Only the host can do that.")
        raise AppError(401, "HOST_SESSION_REQUIRED", "A valid host session is required.")
    if host_game.game_code != code:
        raise AppError(403, "WRONG_GAME", "You do not have access to that game.")
    _release_connection(db)
    return host_game


def require_player_of_game(
    game_code: str, request: Request, db: Session = Depends(get_db)
) -> Player:
    settings = request.app.state.settings
    code = normalize_game_code(game_code)
    player = resolve_player(db, settings, request.cookies.get(settings.player_cookie_name))
    if player is None:
        raise AppError(401, "PLAYER_SESSION_REQUIRED", "A valid player session is required.")
    if player.game.game_code != code:
        raise AppError(403, "WRONG_GAME", "You do not have access to that game.")
    _release_connection(db)
    return player


@dataclass
class LobbyViewer:
    role: str
    game: Game
    player: Player | None = None


def identify_viewer(
    db: Session, settings, cookies, game_code: str, role: str | None = None
) -> LobbyViewer:
    """Single source of truth for "who is this and may they see this game?".

    Used by the REST dependency below AND by the WebSocket handshake, so both paths apply
    exactly the same session rules. ``role`` only *narrows* the choice (a browser can ask to be
    treated as host or player); it can never grant anything the session cookies do not prove.
    """
    code = normalize_game_code(game_code)
    host_game = resolve_host_game(db, settings, cookies.get(settings.host_cookie_name))
    player = resolve_player(db, settings, cookies.get(settings.player_cookie_name))
    if role != "player" and host_game is not None and host_game.game_code == code:
        return LobbyViewer("host", host_game)
    if role != "host" and player is not None and player.game.game_code == code:
        return LobbyViewer("player", player.game, player)
    if host_game is not None or player is not None:
        raise AppError(403, "WRONG_GAME", "You do not have access to that game.")
    raise AppError(401, "SESSION_REQUIRED", "Join or host this game first.")


def lobby_viewer(game_code: str, request: Request, db: Session = Depends(get_db)) -> LobbyViewer:
    """Host or player of THIS game; anyone else is refused."""
    viewer = identify_viewer(db, request.app.state.settings, request.cookies, game_code)
    _release_connection(db)
    return viewer
