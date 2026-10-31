from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import (
    require_host_of_game, require_player, require_player_of_game,
)
from app.db.models import Game, Player
from app.db.session import get_db
from app.errors import AppError
from app.lobby.views import Viewer, build_lobby_view
from app.schemas.lobby import LeaderAssignRequest, LobbyResponse, TeamJoinRequest
from app.schemas.player import LobbyPlayerOut, PlayerLobbyResponse
from app.teams.service import assign_leader, join_team, remove_leader, remove_player

router = APIRouter(tags=["teams"])


# --- Phase 1 minimal view (kept: the new GET /api/games/{code}/lobby supersedes it) --------
def _lobby_player(p: Player) -> LobbyPlayerOut:
    return LobbyPlayerOut(display_name=p.display_name, pawn_color=p.pawn_color, team_id=p.team_id)


@router.get("/api/player/lobby", response_model=PlayerLobbyResponse)
def player_lobby(
    player: Player = Depends(require_player), db: Session = Depends(get_db)
) -> PlayerLobbyResponse:
    others = db.scalars(
        select(Player).where(Player.game_id == player.game_id).order_by(Player.joined_at, Player.id)
    ).all()
    return PlayerLobbyResponse(
        game_code=player.game.game_code,
        status=player.game.status,
        you=_lobby_player(player),
        players=[_lobby_player(p) for p in others],
    )


# --- Phase 2 ------------------------------------------------------------------------------
games = APIRouter(prefix="/api/games/{game_code}", tags=["teams"])


def _host_view(db: Session, request: Request, game: Game) -> LobbyResponse:
    return build_lobby_view(db, request.app.state.settings, Viewer("host", game))


@games.post("/teams/{team_id}/leader", response_model=LobbyResponse)
def set_leader(
    team_id: str,
    payload: LeaderAssignRequest,
    request: Request,
    game: Game = Depends(require_host_of_game),
    db: Session = Depends(get_db),
) -> LobbyResponse:
    assign_leader(
        db, request.app.state.settings, request.app.state.lobby_bus, game.id, team_id, payload.player_id
    )
    return _host_view(db, request, game)


@games.delete("/teams/{team_id}/leader", response_model=LobbyResponse)
def unset_leader(
    team_id: str,
    request: Request,
    game: Game = Depends(require_host_of_game),
    db: Session = Depends(get_db),
) -> LobbyResponse:
    remove_leader(db, request.app.state.lobby_bus, game.id, team_id)
    return _host_view(db, request, game)


@games.post("/teams/join", response_model=LobbyResponse)
def join_team_endpoint(
    payload: TeamJoinRequest,
    request: Request,
    player: Player = Depends(require_player_of_game),
    db: Session = Depends(get_db),
) -> LobbyResponse:
    settings = request.app.state.settings
    limiter = request.app.state.team_code_limiter
    if limiter.is_blocked(player.id):
        raise AppError(429, "TOO_MANY_ATTEMPTS", "Too many wrong team codes. Please wait a minute.")
    try:
        join_team(
            db, settings, request.app.state.lobby_bus, player.game_id, player.id, payload.team_code
        )
    except AppError as exc:
        if exc.code == "INVALID_TEAM_CODE":
            limiter.record_failure(player.id)
        raise
    return build_lobby_view(db, settings, Viewer("player", player.game, player))


@games.post("/players/{player_id}/kick", response_model=LobbyResponse)
def kick_player(
    player_id: str,
    request: Request,
    game: Game = Depends(require_host_of_game),
    db: Session = Depends(get_db),
) -> LobbyResponse:
    target = db.scalar(select(Player).where(Player.id == player_id, Player.game_id == game.id))
    team_id = target.team_id if target is not None else None
    remove_player(db, request.app.state.lobby_bus, game.id, player_id, kicked=True)
    if target is not None and game.status == "RUNNING":
        # a removed player must not keep a puzzle locked for their team
        runtime = request.app.state.game
        runtime.leases.release_player(player_id)
        if team_id:
            from app.lobby.events import LobbyEvent, LobbyEventType
            request.app.state.lobby_bus.publish(LobbyEvent(LobbyEventType.FLOOR_UPDATED, game.id, {}, team_id))
    return _host_view(db, request, game)


class LeaveResponse(BaseModel):
    left: bool


@games.post("/leave", response_model=LeaveResponse)
def leave_lobby(
    request: Request,
    player: Player = Depends(require_player_of_game),
    db: Session = Depends(get_db),
) -> LeaveResponse:
    remove_player(db, request.app.state.lobby_bus, player.game_id, player.id, kicked=False)
    return LeaveResponse(left=True)
