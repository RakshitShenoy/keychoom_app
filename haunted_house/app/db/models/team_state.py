from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.enums import FloorStatus, ObjectStatus, sql_in
from app.db.ids import new_id
from app.db.types import UTCDateTime


class TeamFloorState(Base):
    """Progress of one team on one floor (exactly one row per team/floor)."""

    __tablename__ = "team_floor_state"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    floor_no: Mapped[int] = mapped_column(Integer)
    preset_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default=FloorStatus.LOCKED.value)
    vault_pieces: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    fragment_awarded: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("team_id", "floor_no", name="uq_team_floor_state_team_id_floor_no"),
        CheckConstraint("floor_no >= 1 AND floor_no <= 8", name="floor_no_range"),
        CheckConstraint(sql_in("status", FloorStatus), name="status"),
    )


class ObjectState(Base):
    """State of one interactable object for one team (solved stays solved)."""

    __tablename__ = "object_state"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    floor_no: Mapped[int] = mapped_column(Integer)
    object_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default=ObjectStatus.UNTOUCHED.value)
    solved_by_player_id: Mapped[str | None] = mapped_column(
        ForeignKey("players.id", ondelete="SET NULL"), nullable=True
    )
    solved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    progress: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    __table_args__ = (
        UniqueConstraint(
            "team_id", "floor_no", "object_id", name="uq_object_state_team_id_floor_no_object_id"
        ),
        CheckConstraint("floor_no >= 1 AND floor_no <= 8", name="floor_no_range"),
        CheckConstraint(sql_in("status", ObjectStatus), name="status"),
    )
