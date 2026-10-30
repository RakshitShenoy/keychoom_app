from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.enums import GameStatus, sql_in
from app.db.ids import new_id
from app.db.types import UTCDateTime
from app.timers.clock import utcnow

if TYPE_CHECKING:
    from app.db.models.player import Player
    from app.db.models.team import Team


class Game(Base):
    __tablename__ = "games"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    game_code: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default=GameStatus.LOBBY.value)

    # Host identity: only a keyed hash of the session token is stored.
    host_token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    host_session_expires_at: Mapped[datetime] = mapped_column(UTCDateTime)

    # Host-chosen configuration (validated against global limits in the service layer).
    min_teams: Mapped[int] = mapped_column(Integer)
    max_teams: Mapped[int] = mapped_column(Integer)
    min_players_per_team: Mapped[int] = mapped_column(Integer)
    max_players_per_team: Mapped[int] = mapped_column(Integer)
    base_duration_seconds: Mapped[int] = mapped_column(Integer)

    # Phase 2: the exact number of team slots the host wants (min_teams <= team_count <= max_teams).
    team_count: Mapped[int] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    team_formation_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    team_formation_locked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    # Phase 10: host pause. While ``paused_at`` is set every clock is frozen; on resume all team
    # deadlines move forward by the pause length and ``paused_seconds_total`` keeps the sum, so a
    # deadline recomputed later (a hint) still includes every pause.
    paused_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    paused_seconds_total: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Intentionally no FK yet: games <-> teams would be a circular dependency.
    # The constraint is added together with the winner logic (Phase 2+).
    winner_team_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    players: Mapped[list["Player"]] = relationship(
        back_populates="game", cascade="all, delete-orphan", passive_deletes=True
    )
    teams: Mapped[list["Team"]] = relationship(
        back_populates="game", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint(sql_in("status", GameStatus), name="status"),
        CheckConstraint("min_teams >= 1 AND max_teams >= min_teams", name="team_bounds"),
        CheckConstraint(
            "min_players_per_team >= 1 AND max_players_per_team >= min_players_per_team",
            name="player_bounds",
        ),
        CheckConstraint("base_duration_seconds > 0", name="base_duration_positive"),
        CheckConstraint(
            "team_count >= min_teams AND team_count <= max_teams", name="team_count_bounds"
        ),
        CheckConstraint("paused_seconds_total >= 0", name="paused_total_nonnegative"),
    )
