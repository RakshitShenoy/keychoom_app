from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.ids import new_id
from app.db.types import UTCDateTime
from app.timers.clock import utcnow

if TYPE_CHECKING:
    from app.db.models.game import Game
    from app.db.models.team import Team


class Player(Base):
    __tablename__ = "players"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    team_id: Mapped[str | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )

    display_name: Mapped[str] = mapped_column(String(32))
    # Case-folded copy used for the per-game uniqueness rule.
    display_name_key: Mapped[str] = mapped_column(String(32))

    session_token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    session_expires_at: Mapped[datetime] = mapped_column(UTCDateTime)

    pawn_color: Mapped[str] = mapped_column(String(7))
    is_connected: Mapped[bool] = mapped_column(Boolean, default=False)
    joined_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    game: Mapped["Game"] = relationship(back_populates="players")
    team: Mapped[Optional["Team"]] = relationship(
        back_populates="players", foreign_keys="Player.team_id"
    )

    __table_args__ = (
        UniqueConstraint("game_id", "display_name_key", name="uq_players_game_id_display_name_key"),
    )
