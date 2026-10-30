"""Gameplay rules for Phases 4-6: starting the game, object investigation, challenges, the vault,
the quick quiz, dares, hints and the server-side deadline.

Rules of the road (same as the lobby):
* The server is authoritative. Browsers send intents (an answer, a code, a hint level) and never state.
* Every change of a team's game state runs inside that team's lock, re-reads the rows it needs, writes
  the database (including an audit row) and only AFTER the commit announces the change on the bus.
* Nothing here trusts a team id, object state, time or reward from the browser.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.enums import FloorStatus, GameStatus, ObjectStatus, TeamStatus
from app.db.models import (
    DareConfirmation, Game, HintUsage, ObjectState, Player, QuizAttempt, Team, TeamFloorState,
)
from app.engines import challenge
from app.engines import dare as dare_engine
from app.engines import quiz as quiz_engine
from app.errors import AppError
from app.game import presets as pr
from app.game.runtime import GameRuntime
from app.game.views import active_quiz_attempt, build_floor_state, quiz_attempt_count
from app.hints.penalties import penalty_for_hint
from app.jumpscares import service as scares
from app.lobby.events import LobbyEvent, LobbyEventBus, LobbyEventType, record_event
from app.lobby.locks import game_lock
from app.teams.queries import load_lobby_data
from app.timers.clock import utcnow
from app.timers.deadline import compute_team_deadline, team_deadline_for

log = logging.getLogger(__name__)
E = LobbyEventType
FIRST_FLOOR = 1
TOTAL_FLOORS = pr.TOTAL_FLOORS
FINAL_DOOR_ID = "final_door"


# =====================================================================================
# context + small helpers
# =====================================================================================
@dataclass
class Ctx:
    game: Game
    team: Team
    floor: TeamFloorState
    preset: dict[str, Any]
    objects: dict[str, dict[str, Any]]
    states: dict[str, ObjectState]
    now: datetime
    events: list[LobbyEvent] = field(default_factory=list)

    @property
    def solved(self) -> set[str]:
        return {oid for oid, s in self.states.items() if s.status == ObjectStatus.SOLVED.value and oid != pr.VAULT_ID}

    @property
    def team_name(self) -> str:
        return self.team.name or f"Team {self.team.slot_no}"


def _team_id(player: Player) -> str:
    if player.team_id is None:
        raise AppError(403, "NO_TEAM", "You are not on a team.")
    return player.team_id


def _record(db: Session, ctx: Ctx, event_type: LobbyEventType, payload: dict[str, Any]) -> None:
    """Audit row (same transaction) + the team-scoped event that is announced after the commit."""
    record_event(db, ctx.game.id, event_type, payload, team_id=ctx.team.id)
    ctx.events.append(LobbyEvent(event_type, ctx.game.id, dict(payload), ctx.team.id))


def _publish(runtime: GameRuntime, ctx: Ctx) -> None:
    for event in ctx.events:
        runtime.bus.publish(event)
    runtime.bus.publish(LobbyEvent(E.FLOOR_UPDATED, ctx.game.id, {}, ctx.team.id))
    ctx.events.clear()


def _build_ctx(db: Session, runtime: GameRuntime, game: Game, team: Team, now: datetime) -> Ctx:
    floor = db.scalar(
        select(TeamFloorState)
        .where(TeamFloorState.team_id == team.id, TeamFloorState.floor_no == team.current_floor)
        .execution_options(populate_existing=True)
    )
    if floor is None:
        raise AppError(409, "GAME_NOT_RUNNING", "This team has not entered a floor yet.")
    preset = runtime.content.presets.get(floor.preset_id)
    if preset is None:
        raise AppError(500, "PRESET_MISSING", "This room is unavailable.")
    rows = db.scalars(
        select(ObjectState).where(ObjectState.team_id == team.id, ObjectState.floor_no == floor.floor_no)
        .execution_options(populate_existing=True)
    ).all()
    return Ctx(game, team, floor, preset, pr.objects_by_id(preset), {r.object_id: r for r in rows}, now)


def _load(db: Session, runtime: GameRuntime, player: Player, now: datetime) -> Ctx:
    team_id = _team_id(player)
    game = db.scalar(select(Game).where(Game.id == player.game_id).execution_options(populate_existing=True))
    team = db.scalar(
        select(Team).where(Team.id == team_id, Team.game_id == player.game_id)
        .execution_options(populate_existing=True)
    )
    if game is None or team is None:
        raise AppError(404, "TEAM_NOT_FOUND", "Your team could not be found.")
    _require_running(game)
    return _build_ctx(db, runtime, game, team, now)


def _require_running(game: Game) -> None:
    if game.status == GameStatus.FINISHED.value:
        raise AppError(409, "GAME_OVER", "The game is over.")
    if game.status != GameStatus.RUNNING.value:
        raise AppError(409, "GAME_NOT_RUNNING", "The game is not running.")
    if game.paused_at is not None:  # Phase 10: the host paused the game; nothing may change
        raise AppError(409, "GAME_PAUSED", "The host has paused the game.")


def _eliminate(db: Session, runtime: GameRuntime, ctx: Ctx) -> None:
    """The team's deadline has passed: it can no longer play. Commits and announces."""
    team = ctx.team
    team.status = TeamStatus.ELIMINATED.value
    team.finished_at = ctx.now
    runtime.leases.clear_team(team.id)
    runtime.clear_cooldowns(team.id)
    _record(db, ctx, E.TEAM_ELIMINATED, {"team_name": ctx.team_name})
    db.commit()
    _publish(runtime, ctx)
    _finish_if_nobody_left(db, runtime, ctx.game.id, ctx.now)


def _ensure_playable(db: Session, runtime: GameRuntime, ctx: Ctx) -> None:
    """The server (never the browser) decides whether a team may still play."""
    _require_running(ctx.game)
    team = ctx.team
    if team.status == TeamStatus.ELIMINATED.value:
        raise AppError(409, "TEAM_ELIMINATED", "Your team has run out of time.")
    if team.status != TeamStatus.PLAYING.value:
        raise AppError(409, "TEAM_NOT_PLAYING", "Your team is not playing.")
    if team.deadline_at is not None and ctx.now >= team.deadline_at:
        _eliminate(db, runtime, ctx)
        raise AppError(409, "TEAM_ELIMINATED", "Your team has run out of time.")


def _ensure_floor_open(ctx: Ctx) -> None:
    if ctx.floor.status == FloorStatus.COMPLETED.value:
        raise AppError(409, "FLOOR_COMPLETED", "This floor is already complete.")


def _object_ctx(db, runtime, player, object_id, now) -> tuple[Ctx, dict[str, Any]]:
    ctx = _load(db, runtime, player, now)
    _ensure_playable(db, runtime, ctx)
    _ensure_floor_open(ctx)
    obj = ctx.objects.get(object_id)
    if obj is None:
        raise AppError(404, "OBJECT_NOT_FOUND", "There is nothing like that here.")
    return ctx, obj


def _require_my_lease(runtime: GameRuntime, ctx: Ctx, player: Player, object_id: str):
    lease = runtime.leases.holder(ctx.team.id, object_id, ctx.now)
    if lease is None or lease.player_id != player.id:
        raise AppError(409, "NOT_INVESTIGATING", "You are not investigating that. Investigate it first.")
    return lease


def _state_row(db: Session, ctx: Ctx, object_id: str) -> ObjectState:
    row = ctx.states.get(object_id)
    if row is None:
        row = ObjectState(
            team_id=ctx.team.id, floor_no=ctx.floor.floor_no, object_id=object_id,
            status=ObjectStatus.UNTOUCHED.value, progress={},
        )
        db.add(row)
        db.flush()
        ctx.states[object_id] = row
    return row


def _response(db, runtime, ctx: Ctx, player: Player, result: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"state": build_floor_state(db, runtime, ctx.game, player, ctx.now), "result": result}


def _complete_object(
    db: Session, runtime: GameRuntime, ctx: Ctx, obj: dict[str, Any], row: ObjectState,
    player_id: str | None, display_name: str | None,
) -> str:
    """Mark an object solved for the team, reveal its vault characters and clue. Returns the clue."""
    row.status = ObjectStatus.SOLVED.value
    row.solved_by_player_id = player_id
    row.solved_at = ctx.now
    row.progress = {**(row.progress or {}), "completed": True}
    chars = pr.vault_chars_for(ctx.preset, obj)
    if chars:
        ctx.floor.vault_pieces = {**(ctx.floor.vault_pieces or {}), **chars}
    runtime.leases.release(ctx.team.id, obj["id"])
    text = pr.reward_text(runtime.content, obj)
    _record(db, ctx, E.OBJECT_SOLVED, {
        "object_id": obj["id"], "label": obj["label"], "display_name": display_name, "reveal_text": text,
    })
    if chars:
        _record(db, ctx, E.VAULT_UPDATED, {"revealed": len(ctx.floor.vault_pieces)})
    return text


# =====================================================================================
# Phase 4a: starting the game
# =====================================================================================
def start_game(db: Session, runtime: GameRuntime, game_id: str, now: datetime | None = None) -> Game:
    """Host action: READY -> RUNNING. Every team enters Floor 1 with a room dealt by the server."""
    with game_lock:
        game = db.scalar(select(Game).where(Game.id == game_id).execution_options(populate_existing=True))
        if game is None:
            raise AppError(404, "GAME_NOT_FOUND", "No game exists with that code.")
        if game.status != GameStatus.READY.value:
            raise AppError(
                409, "INVALID_LOBBY_STATE", "The game can only start once team formation is locked."
            )
        data = load_lobby_data(db, game)
        if not data.readiness.requirements_met:
            raise AppError(
                409, "REQUIREMENTS_NOT_MET", "The game cannot start until every requirement is met.",
                [{"code": p.code, "message": p.message} for p in data.readiness.problems],
            )
        presets = pr.presets_for_floor(runtime.content, FIRST_FLOOR)
        if not presets:
            raise AppError(503, "CONTENT_MISSING", "No room is available for Floor 1.")
        dealt = pr.assign_presets([p["id"] for p in presets], [t.id for t in data.teams], seed=f"{game.id}:floor{FIRST_FLOOR}")

        started = now or utcnow()
        game.status = GameStatus.RUNNING.value
        game.started_at = started
        for team in data.teams:
            team.status = TeamStatus.PLAYING.value
            team.current_floor = FIRST_FLOOR
            team.floors_completed = 0
            team.hint_penalty_seconds = 0
            team.deadline_at = compute_team_deadline(started, game.base_duration_seconds, 0)
            db.add(TeamFloorState(
                team_id=team.id, floor_no=FIRST_FLOOR, preset_id=dealt[team.id],
                status=FloorStatus.ACTIVE.value, vault_pieces={}, fragment_awarded=False,
            ))
            scares.plan_for_team(db, runtime.settings, game, team, started)
        event = record_event(db, game.id, E.GAME_STARTED, {})
        db.commit()
        team_ids = [t.id for t in data.teams]
    runtime.bus.publish_change(event)  # everybody's lobby state now says RUNNING
    for team_id in team_ids:
        runtime.bus.publish(LobbyEvent(E.FLOOR_UPDATED, game.id, {}, team_id))
    return game


# =====================================================================================
# Phase 4b: reading state, investigating, releasing
# =====================================================================================
def get_state(db: Session, runtime: GameRuntime, player: Player, now: datetime | None = None) -> dict[str, Any]:
    _team_id(player)
    now = now or utcnow()
    game = db.scalar(select(Game).where(Game.id == player.game_id).execution_options(populate_existing=True))
    state = build_floor_state(db, runtime, game, player, now) if game else None
    if state is None:
        raise AppError(403, "NO_TEAM", "You are not on a team.")
    return {"state": state, "result": None}


def _assign_dare(db: Session, runtime: GameRuntime, ctx: Ctx, obj: dict[str, Any], player: Player) -> None:
    row = _state_row(db, ctx, obj["id"])
    progress = dict(row.progress or {})
    if progress.get("dare_id"):
        return
    attempt = int(progress.get("dare_attempts", 0)) + 1
    pool = runtime.content.dares[obj["dare_pool"]]["dares"]
    chosen = dare_engine.pick_dare(
        pool, dare_engine.dare_seed(ctx.game.id, ctx.team.id, obj["id"], attempt)
    )
    row.progress = {**progress, "dare_id": str(chosen["id"]), "assigned_player_id": player.id, "dare_attempts": attempt}
    row.status = ObjectStatus.IN_PROGRESS.value
    _record(db, ctx, E.DARE_ASSIGNED, {"object_id": obj["id"], "display_name": player.display_name})


def investigate(
    db: Session, runtime: GameRuntime, player: Player, object_id: str, now: datetime | None = None
) -> dict[str, Any]:
    team_id = _team_id(player)
    with runtime.team_lock(team_id):
        now = now or utcnow()
        ctx, obj = _object_ctx(db, runtime, player, object_id, now)
        if obj["type"] == "trap":
            return _spring_trap(db, runtime, ctx, obj, player)
        row = ctx.states.get(object_id)
        if row is not None and row.status == ObjectStatus.SOLVED.value:
            raise AppError(409, "OBJECT_SOLVED", "That has already been solved.")
        if not pr.prerequisites_met(obj, ctx.solved):
            raise AppError(409, "OBJECT_LOCKED", "You cannot reach that yet. Solve something else first.")
        mine = runtime.leases.lease_of_player(ctx.team.id, player.id, now)
        if mine is not None and mine.object_id != object_id:
            raise AppError(
                409, "ALREADY_INVESTIGATING", "Finish or leave your current investigation first."
            )
        outcome, lease = runtime.leases.acquire(ctx.team.id, ctx.floor.floor_no, object_id, player.id, now)
        if outcome == "busy":
            holder = db.get(Player, lease.player_id)
            name = holder.display_name if holder else "A teammate"
            raise AppError(409, "OBJECT_BUSY", f"{name} is already investigating this.", [{"holder": name}])
        try:
            if outcome == "new":
                _state_row(db, ctx, object_id)
                if obj["type"] == "dare":
                    _assign_dare(db, runtime, ctx, obj, player)
                _record(db, ctx, E.OBJECT_LOCKED, {
                    "object_id": object_id, "label": obj["label"], "display_name": player.display_name,
                })
            db.commit()
        except Exception:
            db.rollback()
            runtime.leases.release(ctx.team.id, object_id, player.id)
            raise
        _publish(runtime, ctx)
        return _response(db, runtime, ctx, player)


def _spring_trap(db: Session, runtime: GameRuntime, ctx: Ctx, obj: dict[str, Any], player: Player) -> dict[str, Any]:
    """A trap object only ever produces a jumpscare for the player who touched it. It has no lease and
    no object state: puzzle progress, vault, hints and timers are not read or written."""
    event, payload = scares.trigger_trap(
        db, runtime.settings, ctx.game, ctx.team, player.id, ctx.now, obj.get("scare")
    )
    if event is not None:
        ctx.events.append(event)
    db.commit()
    _publish(runtime, ctx)
    shown = {k: v for k, v in payload.items() if k != "only_player_id"} if payload else None
    return _response(db, runtime, ctx, player, {"result": "TRAP", "message": obj["reward_text"], "jumpscare": shown})


def _on_released(
    db: Session, runtime: GameRuntime, ctx: Ctx, obj: dict[str, Any], display_name: str | None, reason: str
) -> None:
    """Housekeeping when a lease ends without the object being solved."""
    row = ctx.states.get(obj["id"])
    if row is not None and row.status != ObjectStatus.SOLVED.value and obj["type"] == "dare":
        progress = {k: v for k, v in (row.progress or {}).items() if k not in ("dare_id", "assigned_player_id")}
        row.progress = progress
        row.status = ObjectStatus.UNTOUCHED.value  # declined: the next investigator gets a fresh dare
    _record(db, ctx, E.OBJECT_RELEASED, {
        "object_id": obj["id"], "label": obj["label"], "display_name": display_name, "reason": reason,
    })


def release(
    db: Session, runtime: GameRuntime, player: Player, object_id: str, now: datetime | None = None
) -> dict[str, Any]:
    team_id = _team_id(player)
    with runtime.team_lock(team_id):
        now = now or utcnow()
        ctx = _load(db, runtime, player, now)
        obj = ctx.objects.get(object_id)
        if obj is None:
            raise AppError(404, "OBJECT_NOT_FOUND", "There is nothing like that here.")
        lease = runtime.leases.holder(ctx.team.id, object_id, now)
        if lease is None:
            return _response(db, runtime, ctx, player)  # nothing to release: harmless
        if lease.player_id != player.id:
            raise AppError(409, "NOT_INVESTIGATING", "Someone else is investigating that.")
        runtime.leases.release(ctx.team.id, object_id, player.id)
        _on_released(db, runtime, ctx, obj, player.display_name, "released")
        db.commit()
        _publish(runtime, ctx)
        return _response(db, runtime, ctx, player)


def heartbeat(runtime: GameRuntime, player: Player, object_id: str, now: datetime | None = None) -> dict[str, Any]:
    """Keeps the lease alive while the player is really working on the object."""
    team_id = _team_id(player)
    now = now or utcnow()
    lease = runtime.leases.renew(team_id, object_id, player.id, now)
    if lease is None:
        raise AppError(409, "NOT_INVESTIGATING", "You are no longer investigating that.")
    return {"expires_at": lease.expires_at.isoformat(), "server_now": now.isoformat()}


# =====================================================================================
# Phase 4c / 5: answers (single- and multi-step challenges)
# =====================================================================================
def _check_cooldown(runtime: GameRuntime, ctx: Ctx, key: str) -> None:
    wait = runtime.cooldown_remaining(ctx.team.id, key, ctx.now)
    if wait > 0:
        seconds = max(1, math.ceil(wait))
        raise AppError(429, "ANSWER_COOLDOWN", f"Wait {seconds}s before trying again.",
                       [{"retry_after_seconds": seconds}])


def submit_answer(
    db: Session, runtime: GameRuntime, player: Player, object_id: str, answer: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    team_id = _team_id(player)
    with runtime.team_lock(team_id):
        now = now or utcnow()
        ctx, obj = _object_ctx(db, runtime, player, object_id, now)
        if obj["type"] != "puzzle":
            raise AppError(409, "WRONG_OBJECT_TYPE", "That cannot be answered with text.")
        _require_my_lease(runtime, ctx, player, object_id)
        row = _state_row(db, ctx, object_id)
        if row.status == ObjectStatus.SOLVED.value:
            raise AppError(409, "OBJECT_SOLVED", "That has already been solved.")
        _check_cooldown(runtime, ctx, object_id)

        puzzle = runtime.content.puzzles[obj["puzzle"]]
        progress = dict(row.progress or {})
        step = int(progress.get("step", 0))
        grade = challenge.grade_submission(puzzle, step, answer)
        result: dict[str, Any] = {"result": grade.result, "message": grade.message, "step": grade.step + 1}

        if grade.result == challenge.INVALID:
            return _response(db, runtime, ctx, player, result)  # nothing is recorded
        if grade.result == challenge.WRONG:
            attempts = int(progress.get("attempts", 0)) + 1
            row.progress = {**progress, "attempts": attempts}
            row.status = ObjectStatus.IN_PROGRESS.value
            runtime.start_cooldown(ctx.team.id, object_id, now)
            _record(db, ctx, E.ANSWER_WRONG, {
                "object_id": object_id, "display_name": player.display_name, "attempts": attempts,
            })
        elif grade.result == challenge.PARTIAL:
            row.progress = {**progress, "step": step + 1, "attempts": int(progress.get("attempts", 0))}
            row.status = ObjectStatus.IN_PROGRESS.value
            _record(db, ctx, E.CHALLENGE_PROGRESS, {
                "object_id": object_id, "step": step + 2, "step_count": challenge.step_count(puzzle),
                "display_name": player.display_name,
            })
        else:  # CORRECT
            result["reveal_text"] = _complete_object(db, runtime, ctx, obj, row, player.id, player.display_name)
        db.commit()
        _publish(runtime, ctx)
        return _response(db, runtime, ctx, player, result)


# =====================================================================================
# Phase 4d: the vault
# =====================================================================================
def submit_vault(
    db: Session, runtime: GameRuntime, player: Player, code: str, now: datetime | None = None
) -> dict[str, Any]:
    team_id = _team_id(player)
    with runtime.team_lock(team_id):
        now = now or utcnow()
        ctx = _load(db, runtime, player, now)
        _ensure_playable(db, runtime, ctx)
        _ensure_floor_open(ctx)
        if not pr.vault_unlocked(ctx.preset, ctx.solved):
            raise AppError(409, "VAULT_LOCKED", "The vault is still sealed. Keep solving the room.")
        _check_cooldown(runtime, ctx, pr.VAULT_ID)

        guess = challenge.normalize_code(code if isinstance(code, str) else "")
        if len(guess) != pr.VAULT_LENGTH:
            return _response(db, runtime, ctx, player, {
                "result": challenge.INVALID, "message": f"The vault code has {pr.VAULT_LENGTH} characters."})

        row = _state_row(db, ctx, pr.VAULT_ID)
        secret = ctx.preset["vault"]["code"]
        if guess != secret:
            attempts = int((row.progress or {}).get("attempts", 0)) + 1
            row.progress = {**(row.progress or {}), "attempts": attempts}
            runtime.start_cooldown(ctx.team.id, pr.VAULT_ID, now)
            _record(db, ctx, E.ANSWER_WRONG, {
                "object_id": pr.VAULT_ID, "display_name": player.display_name, "attempts": attempts,
            })
            db.commit()
            _publish(runtime, ctx)
            return _response(db, runtime, ctx, player, {
                "result": challenge.WRONG, "message": "The lock does not move. That is not the code."})

        # --- correct: open the vault, award the fragment, complete the floor (all in ONE commit)
        row.status = ObjectStatus.SOLVED.value
        row.solved_by_player_id = player.id
        row.solved_at = now
        floor = ctx.floor
        floor.vault_pieces = {str(i): secret[i] for i in range(pr.VAULT_LENGTH)}
        floor.fragment_awarded = True
        floor.status = FloorStatus.COMPLETED.value
        floor.completed_at = now
        ctx.team.floors_completed = max(ctx.team.floors_completed, floor.floor_no)
        runtime.leases.clear_team(ctx.team.id)
        fragment = ctx.preset["fragment"]
        _record(db, ctx, E.VAULT_OPENED, {"display_name": player.display_name})
        _record(db, ctx, E.FRAGMENT_AWARDED, {"fragment": fragment["label"]})
        _record(db, ctx, E.FLOOR_COMPLETED, {"floor": floor.floor_no})
        db.commit()
        _publish(runtime, ctx)
        return _response(db, runtime, ctx, player, {
            "result": challenge.CORRECT, "message": f"The vault opens. You received the {fragment['label']}!"})


# =====================================================================================
# Phase 7: climbing to the next floor, the final door, the winner
# =====================================================================================
def _team_floor_rows(db: Session, team_id: str) -> dict[int, TeamFloorState]:
    rows = db.scalars(
        select(TeamFloorState).where(TeamFloorState.team_id == team_id).execution_options(populate_existing=True)
    ).all()
    return {r.floor_no: r for r in rows}


def advance_floor(db: Session, runtime: GameRuntime, player: Player, now: datetime | None = None) -> dict[str, Any]:
    """Move the team up one floor. Allowed ONLY when the team's current floor is COMPLETED (vault opened
    and fragment awarded by the server). The next room is dealt by the server; floors cannot be skipped."""
    team_id = _team_id(player)
    with runtime.team_lock(team_id):
        now = now or utcnow()
        ctx = _load(db, runtime, player, now)
        _ensure_playable(db, runtime, ctx)
        floor = ctx.floor
        if floor.status != FloorStatus.COMPLETED.value or not floor.fragment_awarded:
            raise AppError(409, "FLOOR_NOT_COMPLETE", "Open this floor's vault before climbing higher.")
        if floor.floor_no >= TOTAL_FLOORS:
            raise AppError(409, "NO_NEXT_FLOOR", "This is the top floor. Only the final door is left.")
        target = floor.floor_no + 1
        presets = pr.presets_for_floor(runtime.content, target)
        if not presets:
            raise AppError(503, "CONTENT_MISSING", f"No room is available for Floor {target}.")
        team_ids = list(db.scalars(select(Team.id).where(Team.game_id == ctx.game.id)).all())
        dealt = pr.assign_presets([p["id"] for p in presets], team_ids, seed=f"{ctx.game.id}:floor{target}")
        db.add(TeamFloorState(
            team_id=ctx.team.id, floor_no=target, preset_id=dealt[ctx.team.id],
            status=FloorStatus.ACTIVE.value, vault_pieces={}, fragment_awarded=False,
        ))
        ctx.team.current_floor = target
        runtime.leases.clear_team(ctx.team.id)
        title = runtime.content.presets[dealt[ctx.team.id]]["title"]
        try:
            db.flush()
        except IntegrityError:  # a double click: the (team, floor) unique key already has this floor
            db.rollback()
            raise AppError(409, "FLOOR_NOT_COMPLETE", "Your team is already on that floor.") from None
        ctx.floor = db.scalar(
            select(TeamFloorState).where(TeamFloorState.team_id == ctx.team.id, TeamFloorState.floor_no == target)
        )
        _record(db, ctx, E.FLOOR_ENTERED, {"floor": target, "title": title, "display_name": player.display_name})
        db.commit()
        _publish(runtime, ctx)
        return _response(db, runtime, ctx, player, {"result": "ENTERED", "floor": target, "message": f"Floor {target}: {title}"})


def _collected_presets(runtime: GameRuntime, rows: dict[int, TeamFloorState]) -> dict[int, dict[str, Any]] | None:
    """The preset of every floor 1..8 the team has COMPLETED with an awarded fragment (else None)."""
    out: dict[int, dict[str, Any]] = {}
    for floor in range(1, TOTAL_FLOORS + 1):
        row = rows.get(floor)
        if row is None or row.status != FloorStatus.COMPLETED.value or not row.fragment_awarded:
            return None
        preset = runtime.content.presets.get(row.preset_id)
        if preset is None or preset["floor"] != floor:
            return None
        out[floor] = preset
    if len({p["fragment"]["id"] for p in out.values()}) != TOTAL_FLOORS:
        return None
    return out


def open_final_door(
    db: Session, runtime: GameRuntime, player: Player, code: str, now: datetime | None = None
) -> dict[str, Any]:
    """The first team to open the final door wins. The door needs ALL 8 awarded fragments (read from the
    database, never from the browser) and the 8-rune code they spell. The winner is decided under a
    per-game lock, so two teams reaching the door at the same instant produce exactly one winner."""
    team_id = _team_id(player)
    with runtime.team_lock(team_id):
        now = now or utcnow()
        ctx = _load(db, runtime, player, now)
        _ensure_playable(db, runtime, ctx)
        presets = _collected_presets(runtime, _team_floor_rows(db, ctx.team.id))
        if presets is None:
            raise AppError(409, "FINAL_DOOR_LOCKED", "The final door needs the key fragments of all 8 floors.")
        _check_cooldown(runtime, ctx, FINAL_DOOR_ID)
        guess = challenge.normalize_code(code if isinstance(code, str) else "")
        expected = pr.final_door_code(presets)
        if len(guess) != TOTAL_FLOORS:
            return _response(db, runtime, ctx, player, {
                "result": challenge.INVALID, "message": f"The final door takes {TOTAL_FLOORS} runes."})
        if guess != expected:
            runtime.start_cooldown(ctx.team.id, FINAL_DOOR_ID, now)
            _record(db, ctx, E.ANSWER_WRONG, {"object_id": FINAL_DOOR_ID, "display_name": player.display_name, "attempts": 1})
            db.commit()
            _publish(runtime, ctx)
            return _response(db, runtime, ctx, player, {
                "result": challenge.WRONG, "message": "The runes flare red and fade. That is not the right order."})

        with game_lock:  # ---- exactly one winner per game
            game = db.scalar(select(Game).where(Game.id == ctx.game.id).execution_options(populate_existing=True))
            if game is None or game.status != GameStatus.RUNNING.value:
                raise AppError(409, "GAME_OVER", "Another team has already escaped.")
            if game.paused_at is not None:
                raise AppError(409, "GAME_PAUSED", "The host has paused the game.")
            team = ctx.team
            ctx.game = game
            game.status = GameStatus.FINISHED.value
            game.winner_team_id = team.id
            game.ended_at = now
            team.status = TeamStatus.ESCAPED.value
            team.finished_at = now
            runtime.leases.clear_team(team.id)
            _record(db, ctx, E.FINAL_DOOR_OPENED, {"display_name": player.display_name, "team_name": ctx.team_name})
            finished = record_event(db, game.id, E.GAME_FINISHED, {
                "winner_team_name": ctx.team_name, "finished_at": now.isoformat(),
            })
            db.commit()
            other_team_ids = list(db.scalars(select(Team.id).where(Team.game_id == game.id, Team.id != team.id)).all())
        _publish(runtime, ctx)
        runtime.bus.publish_change(finished)  # EVERY connected team and the host hear the winner now
        for other in other_team_ids:
            runtime.bus.publish(LobbyEvent(E.FLOOR_UPDATED, game.id, {}, other))
        return _response(db, runtime, ctx, player, {
            "result": challenge.CORRECT, "message": "The final door swings open. Your team escaped first!"})


def _finish_if_nobody_left(db: Session, runtime: GameRuntime, game_id: str, now: datetime) -> bool:
    """When every team has run out of time and nobody escaped, the game ends without a winner."""
    with game_lock:
        game = db.scalar(select(Game).where(Game.id == game_id).execution_options(populate_existing=True))
        if game is None or game.status != GameStatus.RUNNING.value:
            return False
        statuses = db.scalars(select(Team.status).where(Team.game_id == game_id)).all()
        if any(st != TeamStatus.ELIMINATED.value for st in statuses):
            return False
        game.status = GameStatus.FINISHED.value
        game.ended_at = now
        finished = record_event(db, game.id, E.GAME_FINISHED, {"winner_team_name": None, "finished_at": now.isoformat()})
        db.commit()
    runtime.bus.publish_change(finished)
    return True


# =====================================================================================
# Phase 5: the quick quiz
# =====================================================================================
def _finalize_attempt(
    db: Session, runtime: GameRuntime, ctx: Ctx, obj: dict[str, Any], attempt: QuizAttempt,
    player_id: str | None, display_name: str | None,
) -> tuple[int, bool]:
    spec = obj["quiz"]
    by_id = {str(q["id"]): q for q in runtime.content.quizzes[spec["pool"]]["questions"]}
    questions = {qid: by_id[qid] for qid in attempt.question_ids}
    score = quiz_engine.score_attempt(questions, attempt.answers or {})
    ok = quiz_engine.passed(score, spec.get("pass_score", quiz_engine.DEFAULT_PASS_SCORE))
    attempt.score = score
    attempt.submitted_at = ctx.now
    _record(db, ctx, E.QUIZ_FINISHED, {
        "object_id": obj["id"], "score": score, "total": len(attempt.question_ids), "passed": ok,
    })
    if ok:
        _complete_object(db, runtime, ctx, obj, _state_row(db, ctx, obj["id"]), player_id, display_name)
    return score, ok


def quiz_start(
    db: Session, runtime: GameRuntime, player: Player, object_id: str, now: datetime | None = None
) -> dict[str, Any]:
    team_id = _team_id(player)
    with runtime.team_lock(team_id):
        now = now or utcnow()
        ctx, obj = _object_ctx(db, runtime, player, object_id, now)
        if obj["type"] != "quiz":
            raise AppError(409, "WRONG_OBJECT_TYPE", "That is not a quiz.")
        _require_my_lease(runtime, ctx, player, object_id)
        row = _state_row(db, ctx, object_id)
        if row.status == ObjectStatus.SOLVED.value:
            raise AppError(409, "OBJECT_SOLVED", "That has already been solved.")
        spec = obj["quiz"]
        attempt = active_quiz_attempt(db, ctx.team.id, ctx.floor.floor_no, object_id)
        result: dict[str, Any] | None = None
        if attempt is not None and quiz_engine.is_over(now, attempt.ends_at):
            score, ok = _finalize_attempt(db, runtime, ctx, obj, attempt, player.id, player.display_name)
            result = {"result": "TIME_UP", "score": score, "passed": ok}
            attempt = None
            if ok:  # the answers given before time ran out were enough
                db.commit()
                _publish(runtime, ctx)
                return _response(db, runtime, ctx, player, result)
        if attempt is None:
            number = quiz_attempt_count(db, ctx.team.id, ctx.floor.floor_no, object_id) + 1
            pool = runtime.content.quizzes[spec["pool"]]["questions"]
            ids = quiz_engine.draw_questions(
                pool, spec.get("questions", quiz_engine.DEFAULT_QUESTIONS),
                quiz_engine.attempt_seed(ctx.game.id, ctx.team.id, object_id, number),
            )
            db.add(QuizAttempt(
                team_id=ctx.team.id, floor_no=ctx.floor.floor_no, object_id=object_id,
                question_ids=ids, answers={}, started_at=now,
                ends_at=quiz_engine.ends_at(now, spec.get("time_limit_seconds", quiz_engine.DEFAULT_TIME_LIMIT_SECONDS)),
            ))
            row.status = ObjectStatus.IN_PROGRESS.value
            _record(db, ctx, E.QUIZ_STARTED, {"object_id": object_id, "display_name": player.display_name})
        db.commit()
        _publish(runtime, ctx)
        return _response(db, runtime, ctx, player, result)


def quiz_answer(
    db: Session, runtime: GameRuntime, player: Player, object_id: str, question_id: str, choice: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    team_id = _team_id(player)
    with runtime.team_lock(team_id):
        now = now or utcnow()
        ctx, obj = _object_ctx(db, runtime, player, object_id, now)
        if obj["type"] != "quiz":
            raise AppError(409, "WRONG_OBJECT_TYPE", "That is not a quiz.")
        _require_my_lease(runtime, ctx, player, object_id)
        attempt = active_quiz_attempt(db, ctx.team.id, ctx.floor.floor_no, object_id)
        if attempt is None:
            raise AppError(409, "QUIZ_NOT_STARTED", "Start the quiz first.")

        if quiz_engine.is_over(now, attempt.ends_at):  # the SERVER decides when time is up
            score, ok = _finalize_attempt(db, runtime, ctx, obj, attempt, player.id, player.display_name)
            db.commit()
            _publish(runtime, ctx)
            return _response(db, runtime, ctx, player, {"result": "TIME_UP", "score": score, "passed": ok,
                                                        "total": len(attempt.question_ids)})

        answers = dict(attempt.answers or {})
        expected = attempt.question_ids[len(answers)]
        if question_id != expected:
            raise AppError(409, "WRONG_QUESTION", "That is not the current question.")
        by_id = {str(q["id"]): q for q in runtime.content.quizzes[obj["quiz"]["pool"]]["questions"]}
        if choice not in {str(c["id"]) for c in by_id[expected]["choices"]}:
            raise AppError(422, "INVALID_CHOICE", "Pick one of the answers shown.")
        answers[expected] = choice
        attempt.answers = answers
        if len(answers) >= len(attempt.question_ids):
            score, ok = _finalize_attempt(db, runtime, ctx, obj, attempt, player.id, player.display_name)
            result = {"result": "FINISHED", "score": score, "passed": ok, "total": len(attempt.question_ids)}
        else:
            result = {"result": "RECORDED", "answered": len(answers), "total": len(attempt.question_ids)}
        db.commit()
        _publish(runtime, ctx)
        return _response(db, runtime, ctx, player, result)


# =====================================================================================
# Phase 5: dares (safe, optional, confirmable)
# =====================================================================================
def dare_confirm(
    db: Session, runtime: GameRuntime, player: Player, object_id: str, now: datetime | None = None
) -> dict[str, Any]:
    team_id = _team_id(player)
    with runtime.team_lock(team_id):
        now = now or utcnow()
        ctx, obj = _object_ctx(db, runtime, player, object_id, now)
        if obj["type"] != "dare":
            raise AppError(409, "WRONG_OBJECT_TYPE", "That is not a dare.")
        _require_my_lease(runtime, ctx, player, object_id)
        row = _state_row(db, ctx, object_id)
        if row.status == ObjectStatus.SOLVED.value:
            raise AppError(409, "OBJECT_SOLVED", "That has already been completed.")
        progress = row.progress or {}
        if progress.get("assigned_player_id") != player.id or not progress.get("dare_id"):
            raise AppError(409, "NO_DARE_ASSIGNED", "You have not been given that dare.")
        db.add(DareConfirmation(
            team_id=ctx.team.id, floor_no=ctx.floor.floor_no, dare_id=progress["dare_id"],
            confirmed_by_player_id=player.id, confirmed_at=now,
        ))
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise AppError(409, "DARE_ALREADY_CONFIRMED", "That dare was already confirmed.") from None
        text = _complete_object(db, runtime, ctx, obj, row, player.id, player.display_name)
        _record(db, ctx, E.DARE_CONFIRMED, {"object_id": object_id, "display_name": player.display_name})
        db.commit()
        _publish(runtime, ctx)
        return _response(db, runtime, ctx, player, {"result": "CONFIRMED", "reveal_text": text})


# =====================================================================================
# Phase 6: hints and penalties
# =====================================================================================
def use_hint(
    db: Session, runtime: GameRuntime, player: Player, level: int, now: datetime | None = None
) -> dict[str, Any]:
    """Spend one of the TEAM's hints. ``level`` 1-4 is the hint level; each level exists once per team.

    The penalty comes from the server's schedule and is subtracted from the team's deadline in the
    same transaction that records the hint, so a hint can never be free and never be counted twice.
    """
    team_id = _team_id(player)
    settings = runtime.settings
    with runtime.team_lock(team_id):
        now = now or utcnow()
        ctx = _load(db, runtime, player, now)
        _ensure_playable(db, runtime, ctx)
        _ensure_floor_open(ctx)

        used = list(db.scalars(
            select(HintUsage).where(HintUsage.team_id == ctx.team.id).execution_options(populate_existing=True)
        ).all())
        if len(used) >= settings.max_hints:
            raise AppError(409, "NO_HINTS_LEFT", "Your team has used all of its hints.")
        if not 1 <= level <= settings.max_hints:
            raise AppError(422, "INVALID_HINT_LEVEL", f"Hint level must be between 1 and {settings.max_hints}.")
        if level in {h.hint_number for h in used}:
            raise AppError(409, "HINT_ALREADY_USED", "Your team has already used that hint.")

        target = pr.current_objective(ctx.preset, ctx.solved)
        if target is None:
            raise AppError(409, "NO_HINT_AVAILABLE", "There is nothing left to hint at.")
        penalty = penalty_for_hint(level, settings.hint_penalty_schedule)
        total = ctx.team.hint_penalty_seconds + penalty
        new_deadline = team_deadline_for(ctx.game, total)
        if new_deadline <= now:
            raise AppError(409, "NOT_ENOUGH_TIME", "Your team does not have enough time left for that hint.")

        db.add(HintUsage(
            team_id=ctx.team.id, floor_no=ctx.floor.floor_no, hint_number=level, object_id=target,
            penalty_seconds=penalty, requested_by_player_id=player.id, used_at=now,
        ))
        ctx.team.hint_penalty_seconds = total
        ctx.team.deadline_at = new_deadline
        try:
            db.flush()
        except IntegrityError:  # the (team, floor, level) unique key caught a duplicate
            db.rollback()
            raise AppError(409, "HINT_ALREADY_USED", "Your team has already used that hint.") from None

        ladder = ctx.preset["hints"][target]
        about = ctx.preset["vault"]["label"] if target == pr.VAULT_ID else ctx.objects[target]["label"]
        text = ladder[level - 1]
        _record(db, ctx, E.HINT_USED, {
            "level": level, "penalty_seconds": penalty, "about": about, "text": text,
            "display_name": player.display_name, "hints_remaining": settings.max_hints - len(used) - 1,
            "total_penalty_seconds": total, "deadline_at": new_deadline.isoformat(),
        })
        db.commit()
        _publish(runtime, ctx)
        return _response(db, runtime, ctx, player, {
            "result": "HINT", "level": level, "penalty_seconds": penalty, "about": about, "text": text})


# =====================================================================================
# Phase 10: the host pauses and resumes the game
# =====================================================================================
def _lock_all_teams(db: Session, runtime: GameRuntime, game_id: str):
    """Lock order is always: team locks (sorted by id) first, then the game lock. A final-door
    attempt takes one team lock and then the game lock, so the orders never conflict."""
    from contextlib import ExitStack

    stack = ExitStack()
    for team_id in sorted(db.scalars(select(Team.id).where(Team.game_id == game_id)).all()):
        stack.enter_context(runtime.team_lock(team_id))
    return stack


def pause_game(db: Session, runtime: GameRuntime, game_id: str, now: datetime | None = None) -> Game:
    """Freeze every clock. The pause is a stored fact (``games.paused_at``), enforced by the server on
    every gameplay action and by the sweeper; it is not just a banner in the browser."""
    now = now or utcnow()
    with _lock_all_teams(db, runtime, game_id), game_lock:
        game = db.scalar(select(Game).where(Game.id == game_id).execution_options(populate_existing=True))
        if game is None:
            raise AppError(404, "GAME_NOT_FOUND", "No game exists with that code.")
        if game.status != GameStatus.RUNNING.value:
            raise AppError(409, "GAME_NOT_RUNNING", "Only a running game can be paused.")
        if game.paused_at is not None:
            raise AppError(409, "ALREADY_PAUSED", "The game is already paused.")
        game.paused_at = now
        event = record_event(db, game.id, E.GAME_PAUSED, {"paused_at": now.isoformat()})
        db.commit()
        team_ids = list(db.scalars(select(Team.id).where(Team.game_id == game.id)).all())
    runtime.bus.publish_change(event)
    for team_id in team_ids:
        runtime.bus.publish(LobbyEvent(E.FLOOR_UPDATED, game.id, {}, team_id))
    return game


def resume_game(db: Session, runtime: GameRuntime, game_id: str, now: datetime | None = None) -> Game:
    """Unfreeze: every team keeps exactly the time it had when the pause began."""
    from app.db.models import JumpscareEvent

    now = now or utcnow()
    with _lock_all_teams(db, runtime, game_id), game_lock:
        game = db.scalar(select(Game).where(Game.id == game_id).execution_options(populate_existing=True))
        if game is None:
            raise AppError(404, "GAME_NOT_FOUND", "No game exists with that code.")
        if game.status != GameStatus.RUNNING.value or game.paused_at is None:
            raise AppError(409, "NOT_PAUSED", "The game is not paused.")
        delta = max(timedelta(0), now - game.paused_at)
        seconds = int(round(delta.total_seconds()))
        teams = list(db.scalars(
            select(Team).where(Team.game_id == game.id).execution_options(populate_existing=True)
        ).all())
        team_ids = [t.id for t in teams]
        for team in teams:
            if team.status == TeamStatus.PLAYING.value and team.deadline_at is not None:
                team.deadline_at = team.deadline_at + delta
        for attempt in db.scalars(
            select(QuizAttempt).where(QuizAttempt.team_id.in_(team_ids), QuizAttempt.submitted_at.is_(None))
            .execution_options(populate_existing=True)
        ).all():
            attempt.ends_at = attempt.ends_at + delta
        for scare in db.scalars(
            select(JumpscareEvent).where(JumpscareEvent.team_id.in_(team_ids), JumpscareEvent.fired_at.is_(None))
            .execution_options(populate_existing=True)
        ).all():
            scare.scheduled_at = scare.scheduled_at + delta
        runtime.leases.shift_teams(set(team_ids), delta)
        game.paused_seconds_total = int(game.paused_seconds_total or 0) + seconds
        game.paused_at = None
        event = record_event(db, game.id, E.GAME_RESUMED, {"paused_seconds": seconds})
        db.commit()
    runtime.bus.publish_change(event)
    for team_id in team_ids:
        runtime.bus.publish(LobbyEvent(E.FLOOR_UPDATED, game.id, {}, team_id))
    return game


# =====================================================================================
# Phase 6: expiry (also used by the background sweeper)
# =====================================================================================
def _ctx_for_team(db: Session, runtime: GameRuntime, team_id: str, now: datetime) -> Ctx | None:
    team = db.scalar(select(Team).where(Team.id == team_id).execution_options(populate_existing=True))
    if team is None:
        return None
    game = db.scalar(select(Game).where(Game.id == team.game_id).execution_options(populate_existing=True))
    if game is None or game.status != GameStatus.RUNNING.value or game.paused_at is not None:
        return None  # not running, or paused: the sweeper must not touch a frozen game
    try:
        return _build_ctx(db, runtime, game, team, now)
    except AppError:
        return None


def eliminate_overdue_teams(db: Session, runtime: GameRuntime, now: datetime | None = None) -> list[str]:
    """Mark every PLAYING team whose server deadline has passed as ELIMINATED."""
    now = now or utcnow()
    team_ids = db.scalars(
        select(Team.id).where(
            Team.status == TeamStatus.PLAYING.value,
            Team.deadline_at.is_not(None), Team.deadline_at <= now,
        )
    ).all()
    out: list[str] = []
    for team_id in team_ids:
        with runtime.team_lock(team_id):
            ctx = _ctx_for_team(db, runtime, team_id, now)
            if (ctx is None or ctx.team.status != TeamStatus.PLAYING.value
                    or ctx.team.deadline_at is None or now < ctx.team.deadline_at):
                continue
            _eliminate(db, runtime, ctx)
            out.append(team_id)
    return out


def _sweep_leases(db: Session, runtime: GameRuntime, now: datetime) -> int:
    count = 0
    paused_teams = set(db.scalars(
        select(Team.id).join(Game, Game.id == Team.game_id).where(Game.paused_at.is_not(None))
    ).all())
    for lease in runtime.leases.expire(now, paused_teams):
        with runtime.team_lock(lease.team_id):
            ctx = _ctx_for_team(db, runtime, lease.team_id, now)
            obj = ctx.objects.get(lease.object_id) if ctx else None
            if ctx is None or obj is None:
                continue
            _on_released(db, runtime, ctx, obj, None, "timeout")
            db.commit()
            _publish(runtime, ctx)
            count += 1
    return count


def _sweep_quizzes(db: Session, runtime: GameRuntime, now: datetime) -> int:
    ids = db.scalars(
        select(QuizAttempt.id).where(QuizAttempt.submitted_at.is_(None), QuizAttempt.ends_at <= now)
    ).all()
    count = 0
    for attempt_id in ids:
        attempt = db.get(QuizAttempt, attempt_id, populate_existing=True)
        if attempt is None or attempt.submitted_at is not None:
            continue
        with runtime.team_lock(attempt.team_id):
            ctx = _ctx_for_team(db, runtime, attempt.team_id, now)
            attempt = db.get(QuizAttempt, attempt_id, populate_existing=True)
            obj = ctx.objects.get(attempt.object_id) if ctx and attempt else None
            if ctx is None or obj is None or attempt.submitted_at is not None:
                continue
            _finalize_attempt(db, runtime, ctx, obj, attempt, None, None)
            db.commit()
            _publish(runtime, ctx)
            count += 1
    return count


def sweep(
    session_factory: Callable[[], Session], runtime: GameRuntime, now: datetime | None = None
) -> dict[str, Any]:
    """One pass of the background sweeper: stale leases, overdue quizzes, expired team deadlines."""
    now = now or utcnow()
    db = session_factory()
    try:
        released = _sweep_leases(db, runtime, now)
        quizzes = _sweep_quizzes(db, runtime, now)
        eliminated = eliminate_overdue_teams(db, runtime, now)
        jumpscares = scares.fire_due(db, runtime, now)
        return {"released": released, "quizzes": quizzes, "eliminated": eliminated, "jumpscares": jumpscares}
    finally:
        db.close()
