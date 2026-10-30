from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.auth.router import game_config_out
from app.auth.sessions import set_session_cookie
from app.db.session import get_db
from app.errors import AppError
from app.lobby.events import LobbyEvent, LobbyEventType
from app.lobby.service import create_game, join_game
from app.schemas.game import GameCreateRequest, GameCreateResponse
from app.schemas.player import JoinGameRequest, JoinGameResponse

router = APIRouter(prefix="/api/games", tags=["lobby"])


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("", response_model=GameCreateResponse, status_code=201)
def create_game_endpoint(
    payload: GameCreateRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> GameCreateResponse:
    settings = request.app.state.settings
    game, host_token = create_game(db, settings, payload)
    set_session_cookie(
        response, settings, settings.host_cookie_name, host_token, settings.host_session_ttl_seconds,
        request_is_https=request.url.scheme == "https",
    )
    return GameCreateResponse(
        game_id=game.id,
        game_code=game.game_code,
        status=game.status,
        host_session_established=True,
        host_session_expires_at=game.host_session_expires_at,
        team_count=game.team_count,
        config=game_config_out(game),
    )


@router.post("/join", response_model=JoinGameResponse, status_code=201)
def join_game_endpoint(
    payload: JoinGameRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> JoinGameResponse:
    settings = request.app.state.settings
    limiter = request.app.state.join_failure_limiter
    key = _client_key(request)

    if limiter.is_blocked(key):
        raise AppError(429, "TOO_MANY_ATTEMPTS", "Too many wrong game codes. Please wait a minute.")

    try:
        player, game, token = join_game(db, settings, payload.game_code, payload.display_name)
    except AppError as exc:
        if exc.code == "GAME_NOT_FOUND":
            limiter.record_failure(key)
        raise

    set_session_cookie(
        response, settings, settings.player_cookie_name, token, settings.player_session_ttl_seconds,
        request_is_https=request.url.scheme == "https",
    )
    request.app.state.lobby_bus.publish_change(
        LobbyEvent(
            LobbyEventType.PLAYER_JOINED, game.id,
            {"display_name": player.display_name, "player_id": player.id},
        )
    )
    return JoinGameResponse(
        player_id=player.id,
        game_id=game.id,
        game_code=game.game_code,
        display_name=player.display_name,
        team_id=player.team_id,
        pawn_color=player.pawn_color,
        status=game.status,
        session_established=True,
        session_expires_at=player.session_expires_at,
    )
