from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.ids import new_id
from app.db.types import UTCDateTime
from app.db.models.game import Game  # noqa: F401  (resolves the relationship target)
from app.timers.clock import utcnow


class GameEvent(Base):
    """Append-only audit log of everything meaningful that happens in a game."""

    __tablename__ = "game_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    team_id: Mapped[str | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(48))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    # Gives the unit of work the games -> game_events dependency (insert order).
    game: Mapped["Game"] = relationship()

    __table_args__ = (Index("ix_game_events_game_id_created_at", "game_id", "created_at"),)
