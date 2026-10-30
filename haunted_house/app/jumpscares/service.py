"""Jumpscares: planned at game start, fired by the sweeper, triggered by trap objects.

Everything is decided on the server and stored in ``jumpscare_events``:
* ``plan_for_team`` picks N random moments spread over the game (and distinct artwork);
* ``fire_due`` fires them when the time comes, respecting a per-team cooldown, and only while the
  team is playing and the game is running;
* ``trigger_trap`` fires one for a single player when a trap object is touched.

A jumpscare is a pure *presentation* event: nothing here reads or writes puzzle, object, vault,
hint or timer state, so a scare can never solve, reset or change anything.
"""
from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.enums import GameStatus, TeamStatus
from app.db.models import Game, JumpscareEvent, Team
from app.jumpscares import assets
from app.lobby.events import LobbyEvent, LobbyEventType, record_event

if TYPE_CHECKING:
    from app.config import Settings
    from app.game.runtime import GameRuntime

E = LobbyEventType
START_FRACTION, END_FRACTION = 0.10, 0.85  # scares happen between 10% and 85% of the base duration


def _rng(*parts: str) -> random.Random:
    digest = hashlib.sha256("|".join(parts).encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def plan_times(
    game_id: str, team_id: str, started: datetime, base_seconds: int, count: int, cooldown: float
) -> list[tuple[datetime, str]]:
    """``count`` (moment, asset) pairs: one random moment in each equal slice of the playing window,
    kept at least ``cooldown`` apart where the window allows it, with distinct artwork."""
    if count <= 0:
        return []
    rng = _rng("scare-plan", game_id, team_id)
    low, high = base_seconds * START_FRACTION, base_seconds * END_FRACTION
    width = (high - low) / count
    offsets: list[float] = []
    for i in range(count):
        slice_start, slice_end = low + i * width, low + (i + 1) * width
        earliest = slice_start + 0.15 * width
        if offsets:
            earliest = max(earliest, offsets[-1] + cooldown)
        latest = max(earliest, slice_end - 0.15 * width)
        offsets.append(rng.uniform(earliest, latest))
    pool = list(assets.SCARE_IDS)
    rng.shuffle(pool)
    chosen = [pool[i % len(pool)] for i in range(count)]
    return [(started + timedelta(seconds=o), a) for o, a in zip(offsets, chosen)]


def plan_for_team(db: Session, settings: "Settings", game: Game, team: Team, started: datetime) -> int:
    if not settings.jumpscares_enabled:
        return 0
    plan = plan_times(
        game.id, team.id, started, game.base_duration_seconds,
        settings.jumpscares_per_team, settings.jumpscare_cooldown_seconds,
    )
    for when, asset_id in plan:
        db.add(JumpscareEvent(team_id=team.id, kind="IMAGE", asset_id=asset_id, scheduled_at=when))
    return len(plan)


def _last_fired(db: Session, team_id: str) -> datetime | None:
    return db.scalar(select(func.max(JumpscareEvent.fired_at)).where(JumpscareEvent.team_id == team_id))


def in_cooldown(db: Session, settings: "Settings", team_id: str, now: datetime) -> bool:
    last = _last_fired(db, team_id)
    return last is not None and now < last + timedelta(seconds=settings.jumpscare_cooldown_seconds)


def _payload(settings: "Settings", event: JumpscareEvent, **extra: Any) -> dict[str, Any]:
    return {
        "scare_id": event.id, "kind": event.kind,
        **assets.asset_public(event.asset_id, settings.jumpscare_display_ms), **extra,
    }


def fire_due(db: Session, runtime: "GameRuntime", now: datetime) -> int:
    """Fire every scheduled scare that is due. Returns how many were fired."""
    settings = runtime.settings
    due_ids = db.scalars(
        select(JumpscareEvent.id)
        .join(Team, Team.id == JumpscareEvent.team_id)
        .where(
            JumpscareEvent.fired_at.is_(None), JumpscareEvent.kind == "IMAGE",
            JumpscareEvent.scheduled_at <= now, Team.status == TeamStatus.PLAYING.value,
        )
        .order_by(JumpscareEvent.scheduled_at)
    ).all()
    fired = 0
    for event_id in due_ids:
        team_id = db.scalar(select(JumpscareEvent.team_id).where(JumpscareEvent.id == event_id))
        with runtime.team_lock(team_id):
            event = db.get(JumpscareEvent, event_id, populate_existing=True)
            team = db.get(Team, team_id, populate_existing=True)
            game = db.get(Game, team.game_id, populate_existing=True) if team else None
            if (event is None or event.fired_at is not None or team is None or game is None
                    or game.status != GameStatus.RUNNING.value or game.paused_at is not None
                    or team.status != TeamStatus.PLAYING.value):
                continue
            last = _last_fired(db, team.id)
            if last is not None and now < last + timedelta(seconds=settings.jumpscare_cooldown_seconds):
                event.scheduled_at = last + timedelta(seconds=settings.jumpscare_cooldown_seconds)  # try later
                db.commit()
                continue
            event.fired_at = now
            payload = _payload(settings, event)
            record_event(db, game.id, E.JUMPSCARE, {"asset_id": event.asset_id, "kind": "IMAGE"}, team_id=team.id)
            db.commit()
            runtime.bus.publish(LobbyEvent(E.JUMPSCARE, game.id, payload, team.id))
            fired += 1
    return fired


def trigger_trap(
    db: Session, settings: "Settings", game: Game, team: Team, player_id: str, now: datetime,
    preferred_asset: str | None = None,
) -> tuple[LobbyEvent | None, dict[str, Any] | None]:
    """Caller holds the team lock and commits. Returns (event to publish, payload) - or (None, None)
    while the team is still in its jumpscare cooldown (a trap never spams the team)."""
    if not settings.jumpscares_enabled or in_cooldown(db, settings, team.id, now):
        return None, None
    rng = _rng("trap", game.id, team.id, player_id, now.isoformat())
    asset_id = preferred_asset if preferred_asset and assets.is_known(preferred_asset) else rng.choice(assets.SCARE_IDS)
    event = JumpscareEvent(
        team_id=team.id, target_player_id=player_id, kind="TRAP", asset_id=asset_id,
        scheduled_at=now, fired_at=now,
    )
    db.add(event)
    db.flush()
    record_event(db, game.id, E.TRAP_TRIGGERED, {"asset_id": asset_id}, team_id=team.id)
    payload = _payload(settings, event, only_player_id=player_id)
    return LobbyEvent(E.JUMPSCARE, game.id, payload, team.id), payload


def count_fired(db: Session, team_id: str) -> int:
    return db.scalar(
        select(func.count()).select_from(JumpscareEvent).where(
            JumpscareEvent.team_id == team_id, JumpscareEvent.fired_at.is_not(None)
        )
    ) or 0
