from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.ids import new_id
from app.db.types import UTCDateTime
from app.timers.clock import utcnow


class DareConfirmation(Base):
    """A team's confirmation that a (safe, non-humiliating) dare was performed."""

    __tablename__ = "dare_confirmations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    floor_no: Mapped[int] = mapped_column(Integer)
    dare_id: Mapped[str] = mapped_column(String(64))
    confirmed_by_player_id: Mapped[str | None] = mapped_column(
        ForeignKey("players.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "team_id", "floor_no", "dare_id", name="uq_dare_confirmations_team_id_floor_no_dare_id"
        ),
        CheckConstraint("floor_no >= 1 AND floor_no <= 8", name="floor_no_range"),
    )
