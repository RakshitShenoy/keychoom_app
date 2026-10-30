from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.enums import TeamStatus, sql_in
from app.db.ids import new_id
from app.db.types import UTCDateTime
from app.timers.clock import utcnow

if TYPE_CHECKING:
    from app.db.models.game import Game
    from app.db.models.player import Player


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    # Stable position of the slot inside the game (1..team_count); makes slot creation idempotent.
    slot_no: Mapped[int] = mapped_column(Integer)
    name: Mapped[str | None] = mapped_column(String(40), nullable=True)
    icon: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Leader of this team. teams <-> players reference each other, so both
    # relationships below name their foreign key explicitly.
    leader_player_id: Mapped[str | None] = mapped_column(
        ForeignKey("players.id", ondelete="SET NULL"), nullable=True
    )

    # Assigned when the host approves the leader; unique across all games.
    team_code: Mapped[str | None] = mapped_column(
        String(12), unique=True, index=True, nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default=TeamStatus.FORMING.value)
    current_floor: Mapped[int] = mapped_column(Integer, default=1)
    floors_completed: Mapped[int] = mapped_column(Integer, default=0)

    # Accumulated hint penalties; deadline = start + base duration + this.
    hint_penalty_seconds: Mapped[int] = mapped_column(Integer, default=0)
    deadline_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    game: Mapped["Game"] = relationship(back_populates="teams")
    players: Mapped[list["Player"]] = relationship(
        back_populates="team", foreign_keys="Player.team_id"
    )

    __table_args__ = (
        UniqueConstraint("game_id", "name", name="uq_teams_game_id_name"),
        UniqueConstraint("game_id", "slot_no", name="uq_teams_game_id_slot_no"),
        CheckConstraint("slot_no >= 1", name="slot_no_positive"),
        CheckConstraint(sql_in("status", TeamStatus), name="status"),
        CheckConstraint("current_floor >= 1 AND current_floor <= 8", name="current_floor_range"),
        CheckConstraint(
            "floors_completed >= 0 AND floors_completed <= 8", name="floors_completed_range"
        ),
        CheckConstraint("hint_penalty_seconds >= 0", name="hint_penalty_nonnegative"),
    )
