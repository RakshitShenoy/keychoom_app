"""Team rules: leader approval, joining by team code, and removing players.

Every function re-reads the rows it needs *inside* the process-wide lobby lock, so two
simultaneous requests cannot both claim the last free place or the same leader slot.
Nothing from the browser is trusted: team ids, player ids and codes are always resolved
against the game that the authenticated session belongs to.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.enums import GameStatus
from app.db.models import Game, Player, Team
from app.errors import AppError
from app.lobby.events import LobbyEvent, LobbyEventBus, LobbyEventType, record_event
from app.lobby.locks import game_lock
from app.teams.codes import generate_team_code, normalize_team_code

if TYPE_CHECKING:
    from app.config import Settings

log = logging.getLogger(__name__)


# --- helpers -----------------------------------------------------------------------------
def _team_updated(game_id: str, team: Team) -> LobbyEvent:
    return LobbyEvent(
        LobbyEventType.TEAM_UPDATED, game_id, {"team_id": team.id, "team_name": team.name}
    )



def _fresh_game(db: Session, game_id: str) -> Game:
    game = db.scalar(
        select(Game).where(Game.id == game_id).execution_options(populate_existing=True)
    )
    if game is None:  # cannot normally happen: the caller was authenticated against this row
        raise AppError(404, "GAME_NOT_FOUND", "No game exists with that code.")
    return game


def _require_formation_open(game: Game) -> None:
    if game.status == GameStatus.TEAM_FORMATION.value:
        return
    if game.status == GameStatus.LOBBY.value:
        raise AppError(409, "TEAM_FORMATION_NOT_STARTED", "Team formation has not started yet.")
    if game.status == GameStatus.READY.value:
        raise AppError(409, "TEAM_FORMATION_LOCKED", "Team formation has ended.")
    raise AppError(409, "GAME_NOT_ACCEPTING_PLAYERS", "This game is no longer accepting players.")


def _team_in_game(db: Session, game: Game, team_id: str) -> Team:
    team = db.scalar(
        select(Team)
        .where(Team.id == team_id, Team.game_id == game.id)
        .execution_options(populate_existing=True)
    )
    if team is None:
        raise AppError(404, "TEAM_NOT_FOUND", "No such team in this game.")
    return team


def _player_in_game(db: Session, game: Game, player_id: str) -> Player:
    player = db.scalar(
        select(Player)
        .where(Player.id == player_id, Player.game_id == game.id)
        .execution_options(populate_existing=True)
    )
    if player is None:
        raise AppError(404, "PLAYER_NOT_FOUND", "No such player in this game.")
    return player


def _team_code_taken(db: Session, code: str) -> bool:
    return db.scalar(select(Team.id).where(Team.team_code == code)) is not None


def _new_unique_team_code(db: Session, length: int) -> str:
    for _ in range(50):
        code = generate_team_code(length)
        if not _team_code_taken(db, code):
            return code
    raise AppError(503, "TEAM_CODE_UNAVAILABLE", "Could not allocate a team code.")


# --- host: leaders -----------------------------------------------------------------------
def assign_leader(
    db: Session, settings: "Settings", bus: LobbyEventBus, game_id: str, team_id: str, player_id: str
) -> Team:
    with game_lock:
        game = _fresh_game(db, game_id)
        _require_formation_open(game)
        team = _team_in_game(db, game, team_id)
        player = _player_in_game(db, game, player_id)

        if team.leader_player_id == player.id:
            return team  # retry/refresh of the same request: nothing to do
        if team.leader_player_id is not None:
            raise AppError(409, "TEAM_ALREADY_HAS_LEADER", "That team already has a leader.")

        leads_elsewhere = db.scalar(select(Team.id).where(Team.leader_player_id == player.id))
        if leads_elsewhere is not None:
            raise AppError(409, "PLAYER_ALREADY_LEADER", "That player already leads a team.")
        if player.team_id not in (None, team.id):
            raise AppError(409, "PLAYER_ON_OTHER_TEAM", "That player already belongs to another team.")

        team.leader_player_id = player.id
        player.team_id = team.id
        if team.team_code is None:
            team.team_code = _new_unique_team_code(db, settings.team_code_length)

        event = record_event(
            db, game.id, LobbyEventType.LEADER_ASSIGNED,
            {"team_id": team.id, "player_id": player.id,
             "team_name": team.name, "display_name": player.display_name},
            team_id=team.id,
        )
        db.commit()
    bus.publish_change(event, _team_updated(game.id, team))
    return team


def remove_leader(db: Session, bus: LobbyEventBus, game_id: str, team_id: str) -> Team:
    """Demote the leader to an ordinary member and revoke the team code."""
    with game_lock:
        game = _fresh_game(db, game_id)
        _require_formation_open(game)
        team = _team_in_game(db, game, team_id)
        if team.leader_player_id is None:
            raise AppError(409, "TEAM_HAS_NO_LEADER", "That team has no leader.")
        former = team.leader_player_id
        team.leader_player_id = None
        team.team_code = None  # old code stops working immediately
        event = record_event(
            db, game.id, LobbyEventType.LEADER_REMOVED,
            {"team_id": team.id, "player_id": former, "team_name": team.name}, team_id=team.id,
        )
        db.commit()
    bus.publish_change(event, _team_updated(game.id, team))
    return team


# --- player: join by team code ------------------------------------------------------------
def join_team(
    db: Session, settings: "Settings", bus: LobbyEventBus, game_id: str, player_id: str, raw_code: str
) -> Team:
    with game_lock:
        game = _fresh_game(db, game_id)
        player = _player_in_game(db, game, player_id)

        _require_formation_open(game)
        if player.team_id is not None:
            raise AppError(409, "ALREADY_ON_TEAM", "You are already on a team.")

        code = normalize_team_code(raw_code, settings.team_code_length)
        team = None
        if code:
            # Scoped to THIS game: a valid code from another game simply does not match.
            # FOR UPDATE: a no-op on SQLite (the process-wide lock above serialises us), but on
            # PostgreSQL it makes the capacity check below safe across several workers too.
            team = db.scalar(
                select(Team)
                .where(Team.game_id == game.id, Team.team_code == code)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        if team is None:
            raise AppError(404, "INVALID_TEAM_CODE", "That team code is not valid.")

        size = db.scalar(
            select(func.count()).select_from(Player).where(Player.team_id == team.id)
        ) or 0
        if size >= game.max_players_per_team:
            raise AppError(409, "TEAM_FULL", "That team is full.")

        player.team_id = team.id
        event = record_event(
            db, game.id, LobbyEventType.TEAM_JOINED,
            {"team_id": team.id, "player_id": player.id,
             "team_name": team.name, "display_name": player.display_name},
            team_id=team.id,
        )
        db.commit()
    bus.publish_change(event, _team_updated(game.id, team))
    return team


# --- leaving / kicking --------------------------------------------------------------------
def remove_player(
    db: Session, bus: LobbyEventBus, game_id: str, player_id: str, *, kicked: bool
) -> None:
    """Delete a player (their session dies with the row). Leaders lose their team code."""
    with game_lock:
        game = _fresh_game(db, game_id)
        if game.status == GameStatus.READY.value:
            raise AppError(
                409, "TEAM_FORMATION_LOCKED",
                "Team formation is locked. Reopen it before removing players.",
            )
        in_lobby = game.status in (GameStatus.LOBBY.value, GameStatus.TEAM_FORMATION.value)
        if not in_lobby and not (kicked and game.status == GameStatus.RUNNING.value):
            # Phase 10: the host may still remove a player from a RUNNING game; players cannot leave it.
            raise AppError(409, "GAME_NOT_ACCEPTING_PLAYERS", "The game is no longer in the lobby.")

        player = _player_in_game(db, game, player_id)
        team_id = player.team_id
        name = player.display_name
        team = db.get(Team, team_id) if team_id else None

        led = db.scalar(select(Team).where(Team.leader_player_id == player.id))
        if led is not None:
            led.leader_player_id = None
            led.team_code = None
            db.flush()

        event = record_event(
            db, game.id,
            LobbyEventType.PLAYER_KICKED if kicked else LobbyEventType.PLAYER_LEFT,
            {"player_id": player.id, "display_name": name, "team_id": team_id},
            team_id=team_id,
        )
        db.delete(player)
        db.commit()
    extra = (_team_updated(game.id, team),) if team is not None else ()
    bus.publish_change(event, *extra)
