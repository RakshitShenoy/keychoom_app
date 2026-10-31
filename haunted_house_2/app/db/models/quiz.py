from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.ids import new_id
from app.db.types import UTCDateTime


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    floor_no: Mapped[int] = mapped_column(Integer)
    object_id: Mapped[str] = mapped_column(String(64))
    question_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    answers: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime)
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_quiz_attempts_team_id_floor_no", "team_id", "floor_no"),
        CheckConstraint("floor_no >= 1 AND floor_no <= 8", name="floor_no_range"),
        CheckConstraint("score IS NULL OR score >= 0", name="score_nonnegative"),
    )
