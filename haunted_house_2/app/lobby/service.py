"""Lobby rules: creating games and joining them. No HTTP concerns in here."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.tokens import generate_token, hash_token
from app.db.enums import JOINABLE_GAME_STATUSES, GameStatus
from app.db.ids import new_id
from app.db.models import Game, GameEvent, Player
from app.errors import AppError
from app.lobby.codes import (
    GameCodeGenerationError,
    generate_unique_game_code,
    normalize_game_code,
)
from app.lobby.names import InvalidDisplayName, display_name_key, normalize_display_name
from app.schemas.game import GameCreateRequest
from app.teams.pawns import random_pawn_color
from app.teams.slots import ensure_team_slots
from app.timers.clock import utcnow

if TYPE_CHECKING:
    from app.config import Settings

log = logging.getLogger(__name__)

# Single-process design: this serialises "check capacity, then insert" so two
# simultaneous joins cannot both take the last slot. On PostgreSQL the
# ``SELECT ... FOR UPDATE`` on the game row below provides the same guarantee
# across processes.
_join_lock = threading.Lock()

_CODE_INSERT_ATTEMPTS = 5


@dataclass(frozen=True)
class GameConfig:
    min_teams: int
    max_teams: int
    min_players_per_team: int
    max_players_per_team: int
    base_duration_seconds: int
    team_count: int


def resolve_game_config(settings: "Settings", payload: GameCreateRequest) -> GameConfig:
    """Fill defaults and validate every value against the global limits."""

    def pick(value: int | None, default: int) -> int:
        return default if value is None else value

    cfg = GameConfig(
        min_teams=pick(payload.min_teams, settings.min_teams_limit),
        max_teams=pick(payload.max_teams, settings.max_teams_limit),
        min_players_per_team=pick(payload.min_players_per_team, settings.min_players_limit),
        max_players_per_team=pick(payload.max_players_per_team, settings.max_players_limit),
        base_duration_seconds=pick(payload.base_duration_seconds, settings.default_base_duration_seconds),
        team_count=0,  # filled in below (its default depends on max_teams)
    )
    cfg = GameConfig(**{**cfg.__dict__, "team_count": pick(payload.team_count, cfg.max_teams)})

    errors: list[dict[str, str]] = []

    def check_range(field: str, value: int, low: int, high: int) -> None:
        if not low <= value <= high:
            errors.append({"field": field, "message": f"must be between {low} and {high}"})

    check_range("min_teams", cfg.min_teams, settings.min_teams_limit, settings.max_teams_limit)
    check_range("max_teams", cfg.max_teams, settings.min_teams_limit, settings.max_teams_limit)
    check_range("team_count", cfg.team_count, settings.min_teams_limit, settings.max_teams_limit)
    if cfg.min_teams <= cfg.max_teams and not cfg.min_teams <= cfg.team_count <= cfg.max_teams:
        errors.append(
            {"field": "team_count", "message": "must be between min_teams and max_teams"}
        )
    check_range(
        "min_players_per_team", cfg.min_players_per_team,
        settings.min_players_limit, settings.max_players_limit,
    )
    check_range(
        "max_players_per_team", cfg.max_players_per_team,
        settings.min_players_limit, settings.max_players_limit,
    )
    check_range(
        "base_duration_seconds", cfg.base_duration_seconds,
        settings.min_base_duration_seconds, settings.max_base_duration_seconds,
    )
    if cfg.min_teams > cfg.max_teams:
        errors.append({"field": "max_teams", "message": "must be greater than or equal to min_teams"})
    if cfg.min_players_per_team > cfg.max_players_per_team:
        errors.append(
            {"field": "max_players_per_team",
             "message": "must be greater than or equal to min_players_per_team"}
        )

    if errors:
        raise AppError(422, "INVALID_GAME_CONFIG", "The game configuration is not valid.", errors)
    return cfg


def create_game(db: Session, settings: "Settings", payload: GameCreateRequest) -> tuple[Game, str]:
    """Create a lobby. Returns the game and the *plaintext* host token (cookie only)."""
    cfg = resolve_game_config(settings, payload)
    host_token = generate_token()
    now = utcnow()

    def is_taken(code: str) -> bool:
        return db.scalar(select(Game.id).where(Game.game_code == code)) is not None

    for _ in range(_CODE_INSERT_ATTEMPTS):
        try:
            code = generate_unique_game_code(is_taken, length=settings.game_code_length)
        except GameCodeGenerationError as exc:
            raise AppError(503, "GAME_CODE_UNAVAILABLE", "Could not allocate a game code.") from exc

        game_id = new_id()
        game = Game(
            id=game_id,
            game_code=code,
            status=GameStatus.LOBBY.value,
            host_token_hash=hash_token(host_token, settings.secret_key),
            host_session_expires_at=now + timedelta(seconds=settings.host_session_ttl_seconds),
            min_teams=cfg.min_teams,
            max_teams=cfg.max_teams,
            min_players_per_team=cfg.min_players_per_team,
            max_players_per_team=cfg.max_players_per_team,
            base_duration_seconds=cfg.base_duration_seconds,
            team_count=cfg.team_count,
            created_at=now,
        )
        db.add(game)
        try:
            # The game row must reach the database BEFORE its audit event (foreign key).
            # Flushing explicitly makes that order certain instead of relying on the ORM's
            # internal sort order for classes that are not linked by a relationship().
            db.flush()
            ensure_team_slots(db, game)  # the team slots exist from the moment the lobby does
            db.add(GameEvent(game_id=game_id, event_type="GAME_CREATED", payload={}, created_at=now))
            db.commit()
        except IntegrityError:
            db.rollback()
            if is_taken(code):
                continue  # someone else grabbed this code between our check and insert; retry
            raise  # any other integrity problem is a real bug: surface it, never hide it as 503
        log.info("game created game_id=%s code=%s", game.id, game.game_code)
        return game, host_token

    raise AppError(503, "GAME_CODE_UNAVAILABLE", "Could not allocate a game code.")


def join_game(
    db: Session, settings: "Settings", game_code: str, display_name: str
) -> tuple[Player, Game, str]:
    """Create an anonymous player session. Returns (player, game, plaintext token)."""
    try:
        name = normalize_display_name(
            display_name, settings.min_display_name_length, settings.max_display_name_length
        )
    except InvalidDisplayName as exc:
        raise AppError(422, "INVALID_DISPLAY_NAME", str(exc)) from exc

    code = normalize_game_code(game_code)

    with _join_lock:
        game = db.scalar(select(Game).where(Game.game_code == code).with_for_update())
        if game is None:
            raise AppError(404, "GAME_NOT_FOUND", "No game exists with that code.")

        if game.status not in JOINABLE_GAME_STATUSES:
            raise AppError(
                409, "GAME_NOT_ACCEPTING_PLAYERS", "This game has already started or finished."
            )

        player_count = db.scalar(
            select(func.count()).select_from(Player).where(Player.game_id == game.id)
        ) or 0
        if player_count >= game.team_count * game.max_players_per_team:
            raise AppError(409, "GAME_FULL", "This game has reached its player capacity.")

        key = display_name_key(name)
        taken = db.scalar(
            select(Player.id).where(Player.game_id == game.id, Player.display_name_key == key)
        )
        if taken is not None:
            raise AppError(409, "DISPLAY_NAME_TAKEN", "That display name is already in use here.")

        now = utcnow()
        token = generate_token()
        player_id = new_id()
        player = Player(
            id=player_id,
            game_id=game.id,
            team_id=None,
            display_name=name,
            display_name_key=key,
            session_token_hash=hash_token(token, settings.secret_key),
            session_expires_at=now + timedelta(seconds=settings.player_session_ttl_seconds),
            pawn_color=random_pawn_color(),
            is_connected=False,
            joined_at=now,
            last_seen_at=now,
        )
        db.add(player)
        try:
            db.flush()  # player row first, then the audit event (see create_game)
            db.add(
                GameEvent(
                    game_id=game.id, event_type="PLAYER_JOINED",
                    payload={"player_id": player_id}, created_at=now,
                )
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            name_taken = db.scalar(
                select(Player.id).where(Player.game_id == game.id, Player.display_name_key == key)
            )
            if name_taken is None:
                raise  # not a duplicate name: do not disguise a real error
            raise AppError(
                409, "DISPLAY_NAME_TAKEN", "That display name is already in use here."
            ) from exc

    log.info("player joined game_id=%s player_id=%s", game.id, player.id)
    return player, game, token
