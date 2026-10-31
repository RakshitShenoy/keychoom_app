"""Status vocabularies. Stored as plain strings guarded by CHECK constraints
(portable and migration-friendly, unlike native database enums)."""
from __future__ import annotations

from enum import Enum


class GameStatus(str, Enum):
    LOBBY = "LOBBY"
    TEAM_FORMATION = "TEAM_FORMATION"
    READY = "READY"
    CUTSCENE = "CUTSCENE"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"


class TeamStatus(str, Enum):
    FORMING = "FORMING"
    LOCKED = "LOCKED"
    PLAYING = "PLAYING"
    ESCAPED = "ESCAPED"
    ELIMINATED = "ELIMINATED"


class FloorStatus(str, Enum):
    LOCKED = "LOCKED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"


class ObjectStatus(str, Enum):
    UNTOUCHED = "UNTOUCHED"
    IN_PROGRESS = "IN_PROGRESS"
    SOLVED = "SOLVED"


# Players may join while the game is in one of these states.
JOINABLE_GAME_STATUSES = (GameStatus.LOBBY.value, GameStatus.TEAM_FORMATION.value)


def sql_in(column: str, enum_cls: type[Enum]) -> str:
    values = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({values})"
