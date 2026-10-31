"""Lobby, team-formation controls and chat. Thin HTTP layer over the services."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth.dependencies import (
    LobbyViewer, lobby_viewer, require_host_of_game, require_player_of_game,
)
from app.db.models import Game, Player
from app.db.session import get_db
from app.errors import AppError
from app.lobby.chat import send_chat_message
from app.lobby.formation import (
    lock_team_formation, reopen_team_formation, start_team_formation,
)
from app.lobby.views import Viewer, build_lobby_view
from app.schemas.lobby import ChatSendRequest, LobbyResponse

router = APIRouter(prefix="/api/games/{game_code}", tags=["lobby"])


def _host_view(db: Session, request: Request, game: Game) -> LobbyResponse:
    return build_lobby_view(db, request.app.state.settings, Viewer("host", game))


@router.get("/lobby", response_model=LobbyResponse)
def get_lobby(
    request: Request, viewer: LobbyViewer = Depends(lobby_viewer), db: Session = Depends(get_db)
) -> LobbyResponse:
    return build_lobby_view(
        db, request.app.state.settings, Viewer(viewer.role, viewer.game, viewer.player)
    )


@router.post("/team-formation/start", response_model=LobbyResponse)
def start_formation(
    request: Request, game: Game = Depends(require_host_of_game), db: Session = Depends(get_db)
) -> LobbyResponse:
    game = start_team_formation(db, request.app.state.lobby_bus, game.id)
    return _host_view(db, request, game)


@router.post("/team-formation/lock", response_model=LobbyResponse)
def lock_formation(
    request: Request, game: Game = Depends(require_host_of_game), db: Session = Depends(get_db)
) -> LobbyResponse:
    game = lock_team_formation(db, request.app.state.lobby_bus, game.id)
    return _host_view(db, request, game)


@router.post("/team-formation/reopen", response_model=LobbyResponse)
def reopen_formation(
    request: Request, game: Game = Depends(require_host_of_game), db: Session = Depends(get_db)
) -> LobbyResponse:
    game = reopen_team_formation(db, request.app.state.lobby_bus, game.id)
    return _host_view(db, request, game)


@router.post("/chat", response_model=LobbyResponse, status_code=201)
def post_chat(
    payload: ChatSendRequest,
    request: Request,
    player: Player = Depends(require_player_of_game),
    db: Session = Depends(get_db),
) -> LobbyResponse:
    settings = request.app.state.settings
    limiter = request.app.state.chat_limiter
    if limiter.is_blocked(player.id):
        raise AppError(429, "CHAT_RATE_LIMITED", "You are sending messages too quickly.")
    limiter.record_failure(player.id)  # counts every attempt: this limiter caps message rate
    send_chat_message(db, settings, request.app.state.lobby_bus, player.game_id, player.id, payload.body)
    return build_lobby_view(db, settings, Viewer("player", player.game, player))
