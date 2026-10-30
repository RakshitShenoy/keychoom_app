from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.sessions import resolve_host_game, resolve_player
from app.db.models import Player
from app.db.session import get_db
from app.schemas.game import GameConfigOut
from app.schemas.session import MeHost, MePlayer, MeResponse
from app.timers.clock import utcnow

router = APIRouter(tags=["session"])


def game_config_out(game) -> GameConfigOut:
    return GameConfigOut(
        min_teams=game.min_teams,
        max_teams=game.max_teams,
        min_players_per_team=game.min_players_per_team,
        max_players_per_team=game.max_players_per_team,
        base_duration_seconds=game.base_duration_seconds,
    )


@router.get("/api/me", response_model=MeResponse)
def me(request: Request, db: Session = Depends(get_db)) -> MeResponse:
    """Who am I? Lets pages restore their state after a refresh."""
    settings = request.app.state.settings

    host_view = None
    game = resolve_host_game(db, settings, request.cookies.get(settings.host_cookie_name))
    if game is not None:
        player_count = db.scalar(
            select(func.count()).select_from(Player).where(Player.game_id == game.id)
        )
        host_view = MeHost(
            game_id=game.id,
            game_code=game.game_code,
            status=game.status,
            team_count=game.team_count,
            config=game_config_out(game),
            player_count=player_count or 0,
        )

    player_view = None
    player = resolve_player(db, settings, request.cookies.get(settings.player_cookie_name))
    if player is not None:
        player.last_seen_at = utcnow()
        db.commit()
        player_view = MePlayer(
            player_id=player.id,
            display_name=player.display_name,
            pawn_color=player.pawn_color,
            team_id=player.team_id,
            game_id=player.game_id,
            game_code=player.game.game_code,
            status=player.game.status,
        )

    return MeResponse(host=host_view, player=player_view)
