from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.ids import new_id
from app.db.types import UTCDateTime


class JumpscareEvent(Base):
    """A scheduled (and later fired) jumpscare. Scheduling logic arrives in a later phase."""

    __tablename__ = "jumpscare_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    target_player_id: Mapped[str | None] = mapped_column(
        ForeignKey("players.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(16))  # IMAGE | VIDEO | TRAP
    asset_id: Mapped[str] = mapped_column(String(64))
    scheduled_at: Mapped[datetime] = mapped_column(UTCDateTime)
    fired_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
