"""Phase 13 "Play as Player": when is the host a player, and which screens are closed to them then.

The host keeps their host session (the server still knows who owns the game and can pause/resume it),
but while the game RUNS and the host plays, every route that would show other teams' information
(the dashboard, the host lobby view, the host WebSocket) refuses the host session. The browser's
"host screen" is not what protects this; these checks are.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.enums import GameStatus
from app.db.models import Game, Player

# While the game is in one of these states a playing host is a player and ONLY a player.
HOST_PLAYING_STATUSES = (GameStatus.CUTSCENE.value, GameStatus.RUNNING.value)


def host_player(db: Session, game: Game) -> Player | None:
    return db.scalar(
        select(Player).where(Player.game_id == game.id, Player.is_host_player.is_(True))
        .execution_options(populate_existing=True)
    )


def host_is_playing(db: Session, game: Game) -> bool:
    return bool(
        game.host_plays and game.status in HOST_PLAYING_STATUSES and host_player(db, game) is not None
    )
