from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import require_host
from app.auth.router import game_config_out
from app.db.models import Game, Player
from app.db.session import get_db
from app.schemas.host import HostGameResponse, HostPlayerOut

router = APIRouter(tags=["host"])


@router.get("/api/host/game", response_model=HostGameResponse)
def host_game(game: Game = Depends(require_host), db: Session = Depends(get_db)) -> HostGameResponse:
    """Minimal host view of the lobby (the full dashboard arrives in a later phase)."""
    players = db.scalars(
        select(Player).where(Player.game_id == game.id).order_by(Player.joined_at, Player.id)
    ).all()
    return HostGameResponse(
        game_id=game.id,
        game_code=game.game_code,
        status=game.status,
        team_count=game.team_count,
        config=game_config_out(game),
        capacity=game.team_count * game.max_players_per_team,
        player_count=len(players),
        players=[
            HostPlayerOut(
                display_name=p.display_name,
                pawn_color=p.pawn_color,
                team_id=p.team_id,
                is_connected=p.is_connected,
            )
            for p in players
        ],
    )
