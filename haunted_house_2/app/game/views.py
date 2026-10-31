"""Builds the private floor state a player receives (REST responses and WebSocket FLOOR_STATE).

Everything shown or enabled in the UI is decided here, on the server. Secrets never appear in it:
* puzzle content only for the player who currently holds the object,
* the vault code only one character at a time, after the object that owns it was solved,
* hint text only for hints the team has already used.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.enums import FloorStatus, GameStatus, ObjectStatus, TeamStatus
from app.db.models import (
    Game, GameEvent, HintUsage, ObjectState, Player, QuizAttempt, Team, TeamFloorState,
)
from app.engines import challenge
from app.engines import dare as dare_engine
from app.engines import quiz as quiz_engine
from app.game import presets as pr
from app.game.runtime import GameRuntime
from app.hints.penalties import penalty_for_hint
from app.timers.clock import utcnow
from app.timers.deadline import frozen_now, seconds_remaining

OBJ_AVAILABLE, OBJ_BUSY, OBJ_SOLVED, OBJ_LOCKED = "AVAILABLE", "BEING_INVESTIGATED", "SOLVED", "LOCKED"


def active_quiz_attempt(db: Session, team_id: str, floor_no: int, object_id: str) -> QuizAttempt | None:
    return db.scalar(
        select(QuizAttempt)
        .where(
            QuizAttempt.team_id == team_id, QuizAttempt.floor_no == floor_no,
            QuizAttempt.object_id == object_id, QuizAttempt.submitted_at.is_(None),
        )
        .order_by(QuizAttempt.started_at.desc())
        .execution_options(populate_existing=True)
    )


def quiz_attempt_count(db: Session, team_id: str, floor_no: int, object_id: str) -> int:
    return db.scalar(
        select(func.count()).select_from(QuizAttempt).where(
            QuizAttempt.team_id == team_id, QuizAttempt.floor_no == floor_no,
            QuizAttempt.object_id == object_id,
        )
    ) or 0


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _quiz_view(db, runtime, team, floor_no, obj, now) -> dict[str, Any]:
    spec = obj["quiz"]
    pool = runtime.content.quizzes[spec["pool"]]["questions"]
    by_id = {str(q["id"]): q for q in pool}
    count = spec.get("questions", quiz_engine.DEFAULT_QUESTIONS)
    base = {
        "time_limit_seconds": spec.get("time_limit_seconds", quiz_engine.DEFAULT_TIME_LIMIT_SECONDS),
        "questions": count,
        "pass_score": spec.get("pass_score", quiz_engine.DEFAULT_PASS_SCORE),
        "attempts": quiz_attempt_count(db, team.id, floor_no, obj["id"]),
    }
    attempt = active_quiz_attempt(db, team.id, floor_no, obj["id"])
    if attempt is None:
        return {"state": "READY", **base}
    answered = len(attempt.answers or {})
    if quiz_engine.is_over(now, attempt.ends_at) or answered >= len(attempt.question_ids):
        return {"state": "TIME_UP", **base, "ends_at": _iso(attempt.ends_at), "server_now": _iso(now)}
    question = by_id[attempt.question_ids[answered]]
    return {
        "state": "IN_PROGRESS", **base,
        "ends_at": _iso(attempt.ends_at), "server_now": _iso(now),
        "question": quiz_engine.question_public(question, answered + 1, len(attempt.question_ids)),
    }


def build_floor_state(
    db: Session, runtime: GameRuntime, game: Game, player: Player, now: datetime | None = None
) -> dict[str, Any] | None:
    """The personal floor state of ``player`` (None when they are not on a team)."""
    if player.team_id is None:
        return None
    real_now = now or utcnow()
    now = frozen_now(game, real_now)  # while the host has paused, every clock stands still
    paused = game.paused_at is not None
    settings = runtime.settings
    team = db.scalar(select(Team).where(Team.id == player.team_id).execution_options(populate_existing=True))
    if team is None or team.game_id != game.id:
        return None

    members = list(
        db.scalars(
            select(Player).where(Player.team_id == team.id).order_by(Player.joined_at, Player.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    names = {m.id: m.display_name for m in members}
    leader_id = team.leader_player_id

    expired = team.status == TeamStatus.ELIMINATED.value or (
        team.deadline_at is not None and team.status == TeamStatus.PLAYING.value and now >= team.deadline_at
    )
    state: dict[str, Any] = {
        "game_status": game.status,
        "paused": paused,
        "team": {
            "name": team.name or f"Team {team.slot_no}",
            "status": TeamStatus.ELIMINATED.value if expired else team.status,
            "floor": team.current_floor,
            "floors_completed": team.floors_completed,
        },
        "members": [
            {"name": m.display_name, "pawn_color": m.pawn_color, "connected": m.is_connected,
             "is_me": m.id == player.id, "is_leader": m.id == leader_id}
            for m in members
        ],
        "timer": {
            "deadline_at": _iso(team.deadline_at),
            "server_now": _iso(real_now),
            "paused": paused,
            "base_duration_seconds": game.base_duration_seconds,
            "penalty_seconds": team.hint_penalty_seconds,
            "seconds_remaining": seconds_remaining(team.deadline_at, now) if team.deadline_at else None,
            "expired": expired,
        },
        "room": None,
    }

    # ---- whole-game progress: fragments, the final door, the winner (all derived from the database)
    floor_rows = {
        r.floor_no: r
        for r in db.scalars(
            select(TeamFloorState).where(TeamFloorState.team_id == team.id).execution_options(populate_existing=True)
        ).all()
    }
    fragments = []
    for n in range(1, pr.TOTAL_FLOORS + 1):
        row = floor_rows.get(n)
        got = bool(row and row.fragment_awarded)
        frag_preset = runtime.content.presets.get(row.preset_id) if (row and got) else None
        fragments.append({
            "floor": n, "collected": got,
            "label": frag_preset["fragment"]["label"] if frag_preset else None,
            "rune": frag_preset["fragment"].get("rune") if frag_preset else None,  # unknown until collected
        })
    all_collected = all(f["collected"] for f in fragments)
    winner_team = db.get(Team, game.winner_team_id, populate_existing=True) if game.winner_team_id else None
    state["game"] = {
        "status": game.status, "paused": paused, "total_floors": pr.TOTAL_FLOORS, "ended_at": _iso(game.ended_at),
        "winner": {
            "team_name": winner_team.name or f"Team {winner_team.slot_no}",
            "is_my_team": winner_team.id == team.id,
        } if winner_team else None,
    }
    state["fragments"] = fragments
    state["final_door"] = {
        "status": "OPENED" if team.status == TeamStatus.ESCAPED.value else (
            "AVAILABLE" if (all_collected and game.status == GameStatus.RUNNING.value and not expired and not paused) else "LOCKED"),
        "length": pr.TOTAL_FLOORS,
        "collected": sum(1 for f in fragments if f["collected"]),
        "runes": [f["rune"] for f in fragments],
    }
    state["can_advance"] = False

    floor = db.scalar(
        select(TeamFloorState)
        .where(TeamFloorState.team_id == team.id, TeamFloorState.floor_no == team.current_floor)
        .execution_options(populate_existing=True)
    )
    preset = runtime.content.presets.get(floor.preset_id) if floor else None
    if floor is None or preset is None:
        return state

    rows = db.scalars(
        select(ObjectState).where(ObjectState.team_id == team.id, ObjectState.floor_no == floor.floor_no)
        .execution_options(populate_existing=True)
    ).all()
    states = {r.object_id: r for r in rows}
    solved = {oid for oid, r in states.items() if r.status == ObjectStatus.SOLVED.value and oid != pr.VAULT_ID}
    leases = {l.object_id: l for l in runtime.leases.active_for_team(team.id, now)}
    my_lease = next((l for l in leases.values() if l.player_id == player.id), None)

    objects_out = []
    for obj in preset["objects"]:
        oid = obj["id"]
        lease = leases.get(oid)
        if oid in solved:
            status = OBJ_SOLVED
        elif lease is not None:
            status = OBJ_BUSY
        elif not pr.prerequisites_met(obj, solved):
            status = OBJ_LOCKED
        else:
            status = OBJ_AVAILABLE
        objects_out.append({
            "id": oid, "type": "curio" if obj["type"] == "trap" else obj["type"], "label": obj["label"], "icon": obj.get("icon", ""),
            "x": obj["position"]["x"], "y": obj["position"]["y"], "optional": pr.is_optional(obj) and obj["type"] != "trap",  # a trap looks like any other object
            "status": status,
            "holder": names.get(lease.player_id) if lease else None,
            "held_by_me": bool(lease and lease.player_id == player.id),
        })

    completed = floor.status == FloorStatus.COMPLETED.value
    by_player = {l.player_id: l for l in leases.values()}
    label_of = {o["id"]: o["label"] for o in preset["objects"]}
    for member, entry in zip(members, state["members"]):
        held = by_player.get(member.id)
        entry["investigating"] = label_of.get(held.object_id) if held else None
        entry["investigating_id"] = held.object_id if held else None
    required = [o for o in preset["objects"] if not pr.is_optional(o)]
    state["progress"] = {
        "floor": floor.floor_no, "floors_completed": team.floors_completed, "total_floors": pr.TOTAL_FLOORS,
        "required_total": len(required), "required_solved": sum(1 for o in required if o["id"] in solved),
    }
    state["can_advance"] = bool(
        completed and floor.floor_no < pr.TOTAL_FLOORS and team.status == TeamStatus.PLAYING.value
        and game.status == GameStatus.RUNNING.value and not expired and not paused
    )
    vault_def = preset["vault"]
    vault_state = states.get(pr.VAULT_ID)
    pieces = floor.vault_pieces or {}
    if completed:
        vault_status = "OPEN"
    elif pr.vault_unlocked(preset, solved):
        vault_status = "AVAILABLE"
    else:
        vault_status = "LOCKED"
    state["room"] = {
        "floor": floor.floor_no,
        "floor_status": floor.status,
        "title": preset["title"],
        "intro": preset["intro"],
        "objects": objects_out,
        "vault": {
            "label": vault_def["label"], "x": vault_def["position"]["x"], "y": vault_def["position"]["y"],
            "length": pr.VAULT_LENGTH, "status": vault_status,
            "revealed": [pieces.get(str(i)) for i in range(pr.VAULT_LENGTH)],
            "attempts": (vault_state.progress or {}).get("attempts", 0) if vault_state else 0,
        },
        "clues": [
            {"object_id": o["id"], "label": o["label"], "text": pr.reward_text(runtime.content, o)}
            for o in preset["objects"] if o["id"] in solved
        ],
        "fragment": {
            "id": preset["fragment"]["id"], "label": preset["fragment"]["label"],
            "awarded": bool(floor.fragment_awarded),
        },
    }

    # ---- hints: levels, penalties, and the text of the hints already used (team-wide)
    used = list(
        db.scalars(
            select(HintUsage).where(HintUsage.team_id == team.id).order_by(HintUsage.used_at, HintUsage.hint_number)
            .execution_options(populate_existing=True)
        ).all()
    )
    used_levels = {h.hint_number for h in used}
    schedule = settings.hint_penalty_schedule
    revealed = []
    for h in used:
        row = floor_rows.get(h.floor_no)
        used_preset = runtime.content.presets.get(row.preset_id) if row else None  # the room the hint was about
        ladder = (used_preset.get("hints", {}).get(h.object_id or "") or []) if used_preset else []
        if used_preset is None:
            label = ""
        elif h.object_id == pr.VAULT_ID:
            label = used_preset["vault"]["label"]
        else:
            label = next((o["label"] for o in used_preset["objects"] if o["id"] == h.object_id), "")
        revealed.append({
            "level": h.hint_number, "penalty_seconds": h.penalty_seconds, "about": label, "floor": h.floor_no,
            "text": ladder[h.hint_number - 1] if len(ladder) >= h.hint_number else "",
            "used_by": names.get(h.requested_by_player_id),
        })
    state["hints"] = {
        "max": settings.max_hints,
        "used": len(used),
        "remaining": max(0, settings.max_hints - len(used)),
        "levels": [
            {"level": n, "penalty_seconds": penalty_for_hint(n, schedule), "used": n in used_levels}
            for n in range(1, settings.max_hints + 1)
        ],
        "revealed": revealed,
        "total_penalty_seconds": team.hint_penalty_seconds,
        "available": (not expired) and (not completed) and game.status == GameStatus.RUNNING.value
        and not paused and len(used) < settings.max_hints,
    }

    # ---- what THIS player is investigating (puzzle content goes to the holder only)
    state["my_investigation"] = None
    if my_lease is not None and my_lease.object_id in {o["id"] for o in preset["objects"]}:
        obj = pr.objects_by_id(preset)[my_lease.object_id]
        obj_state = states.get(obj["id"])
        progress = (obj_state.progress if obj_state else None) or {}
        inv: dict[str, Any] = {
            "object_id": obj["id"], "label": obj["label"], "type": obj["type"],
            "expires_at": _iso(my_lease.expires_at),
        }
        if obj["type"] == "puzzle":
            inv["challenge"] = challenge.public_view(runtime.content.puzzles[obj["puzzle"]], progress.get("step", 0))
        elif obj["type"] == "quiz":
            inv["quiz"] = _quiz_view(db, runtime, team, floor.floor_no, obj, now)
        elif obj["type"] == "dare":
            dare_id = progress.get("dare_id")
            pool = runtime.content.dares[obj["dare_pool"]]["dares"]
            chosen = next((d for d in pool if str(d["id"]) == dare_id), None)
            inv["dare"] = dare_engine.dare_public(chosen) if chosen else None
        state["my_investigation"] = inv
    db.commit()  # reads are done: hand the connection back now
    return state


_FEED_KEYS = ("display_name", "label", "floor", "title", "level", "team_name", "score", "total", "passed", "paused_seconds")
_FEED_TEXT = {
    "GAME_STARTED": "The game started", "GAME_PAUSED": "The game was paused", "GAME_RESUMED": "The game resumed",
    "GAME_FINISHED": "The game ended", "FLOOR_ENTERED": "entered a new floor", "OBJECT_SOLVED": "solved an object",
    "VAULT_OPENED": "opened the vault", "FRAGMENT_AWARDED": "collected a key fragment",
    "FLOOR_COMPLETED": "completed the floor", "HINT_USED": "used a hint", "TEAM_ELIMINATED": "ran out of time",
    "FINAL_DOOR_OPENED": "opened the final door", "PLAYER_KICKED": "was removed by the host",
    "OBJECT_LOCKED": "started investigating", "TRAP_TRIGGERED": "set off a trap", "JUMPSCARE": "was scared",
    "QUIZ_FINISHED": "finished a quiz", "DARE_CONFIRMED": "completed a dare",
}


def _clean_name(team: Team | None) -> str | None:
    return (team.name or f"Team {team.slot_no}") if team else None


def build_host_dashboard(db: Session, runtime: GameRuntime, game: Game, now: datetime | None = None) -> dict[str, Any]:
    """Everything the host needs to follow the whole game without entering a team's screen.

    Host-only (the routes and the WebSocket check the host session). It never contains puzzle answers,
    vault codes, rune values, or the text of hints: only where each team is and how it is doing."""
    real_now = now or utcnow()
    now = frozen_now(game, real_now)
    paused = game.paused_at is not None
    paused_total = int(game.paused_seconds_total or 0)
    started = game.started_at
    teams = list(
        db.scalars(select(Team).where(Team.game_id == game.id).order_by(Team.slot_no).execution_options(populate_existing=True)).all()
    )
    team_ids = [t.id for t in teams]
    players = list(db.scalars(
        select(Player).where(Player.game_id == game.id).order_by(Player.joined_at, Player.id).execution_options(populate_existing=True)
    ).all())
    by_team: dict[str, list[Player]] = {t.id: [] for t in teams}
    for p in players:
        if p.team_id in by_team:
            by_team[p.team_id].append(p)
    hint_counts = dict(
        db.execute(select(HintUsage.team_id, func.count()).where(HintUsage.team_id.in_(team_ids)).group_by(HintUsage.team_id)).all()
    ) if teams else {}
    floor_rows: dict[str, dict[int, TeamFloorState]] = {t.id: {} for t in teams}
    if teams:
        for r in db.scalars(select(TeamFloorState).where(TeamFloorState.team_id.in_(team_ids)).execution_options(populate_existing=True)).all():
            floor_rows[r.team_id][r.floor_no] = r
    solved_rows: dict[tuple[str, int], set[str]] = {}
    if teams:
        for r in db.scalars(select(ObjectState).where(
            ObjectState.team_id.in_(team_ids), ObjectState.status == ObjectStatus.SOLVED.value
        ).execution_options(populate_existing=True)).all():
            solved_rows.setdefault((r.team_id, r.floor_no), set()).add(r.object_id)

    max_hints = runtime.settings.max_hints
    rows = []
    for t in teams:
        status = t.status
        expired = status == TeamStatus.ELIMINATED.value or (
            status == TeamStatus.PLAYING.value and t.deadline_at is not None and now >= t.deadline_at)
        if expired:
            status = TeamStatus.ELIMINATED.value
        members = by_team[t.id]
        names = {m.id: m.display_name for m in members}
        leases = runtime.leases.active_for_team(t.id, now) if game.status == GameStatus.RUNNING.value else []
        floor = floor_rows[t.id].get(t.current_floor)
        preset = runtime.content.presets.get(floor.preset_id) if floor else None
        solved = solved_rows.get((t.id, t.current_floor), set()) - {pr.VAULT_ID}
        room = task = None
        investigating: list[dict[str, Any]] = []
        required_total = required_solved = 0
        if preset is not None:
            labels = {o["id"]: o["label"] for o in preset["objects"]}
            required = [o for o in preset["objects"] if not pr.is_optional(o)]
            required_total, required_solved = len(required), sum(1 for o in required if o["id"] in solved)
            room = {"title": preset["title"], "preset_id": preset["id"], "floor_status": floor.status}
            if floor.status == FloorStatus.COMPLETED.value:
                task = "Floor complete: fragment collected" if floor.fragment_awarded else "Floor complete"
            else:
                target = pr.current_objective(preset, solved)
                task = (preset["vault"]["label"] if target == pr.VAULT_ID else labels.get(target)) if target else None
            investigating = [
                {"object_id": l.object_id, "label": labels.get(l.object_id, l.object_id), "player": names.get(l.player_id)}
                for l in leases if l.object_id in labels
            ]
        fragments = [bool(floor_rows[t.id].get(n) and floor_rows[t.id][n].fragment_awarded) for n in range(1, pr.TOTAL_FLOORS + 1)]
        all_frags = all(fragments)
        if status == TeamStatus.ESCAPED.value:
            where = f"Floor {t.current_floor} -> escaped through the final door"
        elif status == TeamStatus.ELIMINATED.value:
            where = f"Floor {t.current_floor} -> out of time"
        elif room is None:
            where = f"Floor {t.current_floor}"
        elif all_frags and t.current_floor == pr.TOTAL_FLOORS and floor.status == FloorStatus.COMPLETED.value:
            where = f"Floor {t.current_floor} -> {room['title']} -> at the final door"
        elif floor.status == FloorStatus.COMPLETED.value:
            where = f"Floor {t.current_floor} -> {room['title']} -> ready to climb"
        else:
            doing = investigating[0]["label"] if investigating else (task or "searching")
            verb = "investigating" if investigating else "next"
            where = f"Floor {t.current_floor} -> {room['title']} -> {doing} ({verb})"
        elapsed = None
        if started is not None:
            end = (t.finished_at if t.finished_at is not None else now)
            elapsed = max(0, int((end - started).total_seconds()) - paused_total)
        rows.append({
            "team_id": t.id, "name": _clean_name(t), "icon": t.icon, "status": status,
            "leader": next((m.display_name for m in members if m.id == t.leader_player_id), None),
            "members": [{"name": m.display_name, "pawn_color": m.pawn_color, "connected": m.is_connected,
                         "is_leader": m.id == t.leader_player_id} for m in members],
            "players": len(members), "connected": sum(1 for m in members if m.is_connected),
            "floor": t.current_floor, "floors_completed": t.floors_completed,
            "room": room, "task": task, "investigating": investigating, "where": where,
            "required_solved": required_solved, "required_total": required_total,
            "fragments": sum(fragments), "fragment_flags": fragments,
            "at_final_door": bool(all_frags and status == TeamStatus.PLAYING.value),
            "hints_used": hint_counts.get(t.id, 0), "hints_remaining": max(0, max_hints - hint_counts.get(t.id, 0)),
            "penalty_seconds": t.hint_penalty_seconds,
            "deadline_at": _iso(t.deadline_at),
            "seconds_remaining": seconds_remaining(t.deadline_at, now) if (t.deadline_at and not expired and status == TeamStatus.PLAYING.value) else 0,
            "elapsed_seconds": elapsed,
            "finished_at": _iso(t.finished_at),
        })
    winner_row = next((r for r in rows if r["team_id"] == game.winner_team_id), None)
    winner = None
    if winner_row:
        winner = {"team_name": winner_row["name"], "team_id": winner_row["team_id"],
                  "completion_seconds": winner_row["elapsed_seconds"], "finished_at": winner_row["finished_at"]}

    events = db.execute(
        select(GameEvent, Team).outerjoin(Team, Team.id == GameEvent.team_id)
        .where(GameEvent.game_id == game.id, GameEvent.event_type.in_(list(_FEED_TEXT)))
        .order_by(GameEvent.created_at.desc(), GameEvent.id.desc()).limit(30)
    ).all()
    feed = [{
        "type": ev.event_type, "at": _iso(ev.created_at), "team": _clean_name(team), "text": _FEED_TEXT[ev.event_type],
        "detail": {k: v for k, v in (ev.payload or {}).items() if k in _FEED_KEYS},
    } for ev, team in events]
    db.commit()  # reads are done: hand the connection back now
    return {
        "game_status": game.status, "paused": paused, "paused_at": _iso(game.paused_at),
        "paused_seconds_total": paused_total,
        "server_now": _iso(real_now), "started_at": _iso(game.started_at), "ended_at": _iso(game.ended_at),
        "base_duration_seconds": game.base_duration_seconds, "total_floors": pr.TOTAL_FLOORS,
        "players": {"total": len(players), "connected": sum(1 for p in players if p.is_connected)},
        "winner": winner, "teams": rows, "feed": feed,
    }
