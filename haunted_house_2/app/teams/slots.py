from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.enums import TeamStatus
from app.db.models import Game, Team
from app.teams.names import default_team_icon, default_team_name


def ensure_team_slots(db: Session, game: Game) -> list[Team]:
    """Create any missing team slots (1..team_count). Idempotent: safe to call repeatedly.

    The unique (game_id, slot_no) constraint is the final guard against duplicates.
    The game row must already be flushed/committed (foreign key).
    """
    existing = {
        t.slot_no: t for t in db.scalars(select(Team).where(Team.game_id == game.id)).all()
    }
    created = False
    for slot_no in range(1, game.team_count + 1):
        if slot_no in existing:
            continue
        team = Team(
            game_id=game.id,
            slot_no=slot_no,
            name=default_team_name(slot_no),
            icon=default_team_icon(slot_no),
            status=TeamStatus.FORMING.value,
        )
        db.add(team)
        existing[slot_no] = team
        created = True
    if created:
        db.flush()
    return [existing[n] for n in sorted(existing)]
