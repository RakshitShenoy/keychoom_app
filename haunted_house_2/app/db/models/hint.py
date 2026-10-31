from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.ids import new_id
from app.db.types import UTCDateTime
from app.timers.clock import utcnow


class HintUsage(Base):
    """One row per hint a team has consumed.

    The unique key is (team, floor, hint number). The *total* of 4 hints per team
    is enforced by the hint service in a later phase.
    """

    __tablename__ = "hint_usage"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    floor_no: Mapped[int] = mapped_column(Integer)
    hint_number: Mapped[int] = mapped_column(Integer)
    object_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    penalty_seconds: Mapped[int] = mapped_column(Integer)
    requested_by_player_id: Mapped[str | None] = mapped_column(
        ForeignKey("players.id", ondelete="SET NULL"), nullable=True
    )
    used_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "team_id", "floor_no", "hint_number", name="uq_hint_usage_team_id_floor_no_hint_number"
        ),
        CheckConstraint("floor_no >= 1 AND floor_no <= 8", name="floor_no_range"),
        CheckConstraint("hint_number >= 1 AND hint_number <= 4", name="hint_number_range"),
        CheckConstraint("penalty_seconds >= 0", name="penalty_nonnegative"),
    )
