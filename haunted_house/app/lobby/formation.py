"""Lobby state machine.

    LOBBY (WAITING) --start--> TEAM_FORMATION --lock--> READY (= locked and ready to start)
                                     ^                        |
                                     +-------- reopen --------+

Locking is only allowed when every readiness requirement is met, so "formation locked" and
"ready to start" are the same stored state (GameStatus.READY). Phase 4 will start the game from it.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.enums import GameStatus, TeamStatus
from app.db.models import Game
from app.errors import AppError
from app.lobby.events import LobbyEventBus, LobbyEventType, record_event
from app.lobby.locks import game_lock
from app.teams.queries import load_lobby_data
from app.teams.slots import ensure_team_slots
from app.timers.clock import utcnow


def _fresh_game(db: Session, game_id: str) -> Game:
    game = db.scalar(
        select(Game).where(Game.id == game_id).execution_options(populate_existing=True)
    )
    if game is None:
        raise AppError(404, "GAME_NOT_FOUND", "No game exists with that code.")
    return game


def lobby_state_name(game: Game) -> str:
    """Public name of the lobby state, as shown by the API and the UI."""
    return {
        GameStatus.LOBBY.value: "WAITING",
        GameStatus.TEAM_FORMATION.value: "TEAM_FORMATION",
        GameStatus.READY.value: "READY_TO_START",
    }.get(game.status, game.status)


def start_team_formation(db: Session, bus: LobbyEventBus, game_id: str) -> Game:
    with game_lock:
        game = _fresh_game(db, game_id)
        if game.status != GameStatus.LOBBY.value:
            raise AppError(
                409, "INVALID_LOBBY_STATE", "Team formation can only be started from the waiting lobby."
            )
        ensure_team_slots(db, game)  # also repairs games created before team slots existed
        game.status = GameStatus.TEAM_FORMATION.value
        game.team_formation_started_at = utcnow()
        event = record_event(db, game.id, LobbyEventType.TEAM_FORMATION_STARTED, {})
        db.commit()
    bus.publish_change(event)
    return game


def lock_team_formation(db: Session, bus: LobbyEventBus, game_id: str) -> Game:
    with game_lock:
        game = _fresh_game(db, game_id)
        if game.status != GameStatus.TEAM_FORMATION.value:
            raise AppError(
                409, "INVALID_LOBBY_STATE", "Team formation can only be locked while it is open."
            )
        data = load_lobby_data(db, game)
        if not data.readiness.requirements_met:
            raise AppError(
                409,
                "REQUIREMENTS_NOT_MET",
                "Team formation cannot be locked until every requirement is met.",
                [{"code": p.code, "message": p.message} for p in data.readiness.problems],
            )
        game.status = GameStatus.READY.value
        game.team_formation_locked_at = utcnow()
        for team in data.teams:
            team.status = TeamStatus.LOCKED.value
        event = record_event(db, game.id, LobbyEventType.TEAM_FORMATION_LOCKED, {})
        db.commit()
    bus.publish_change(event)
    return game


def reopen_team_formation(db: Session, bus: LobbyEventBus, game_id: str) -> Game:
    with game_lock:
        game = _fresh_game(db, game_id)
        if game.status != GameStatus.READY.value:
            raise AppError(409, "INVALID_LOBBY_STATE", "Team formation is not locked.")
        data = load_lobby_data(db, game)
        game.status = GameStatus.TEAM_FORMATION.value
        game.team_formation_locked_at = None
        for team in data.teams:
            team.status = TeamStatus.FORMING.value
        event = record_event(db, game.id, LobbyEventType.TEAM_FORMATION_REOPENED, {})
        db.commit()
    bus.publish_change(event)
    return game
