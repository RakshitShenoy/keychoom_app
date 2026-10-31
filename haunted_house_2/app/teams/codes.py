"""Team codes: ``TEAM-7K4P``. Random, easy to type, no look-alike characters."""
from __future__ import annotations

import secrets

from app.lobby.codes import GAME_CODE_ALPHABET

TEAM_CODE_PREFIX = "TEAM-"


def generate_team_code(length: int = 4) -> str:
    if length < 1:
        raise ValueError("length must be positive")
    return TEAM_CODE_PREFIX + "".join(secrets.choice(GAME_CODE_ALPHABET) for _ in range(length))


def normalize_team_code(raw: str, length: int = 4) -> str:
    """Accepts '7k4p', 'team 7k4p' or 'TEAM-7K4P'; returns the canonical form (or '')."""
    if not isinstance(raw, str):
        return ""
    compact = "".join(ch for ch in raw.upper() if ch not in " -_\t\n\r")
    if len(compact) == length + 4 and compact.startswith("TEAM"):
        compact = compact[4:]
    if not compact:
        return ""
    return TEAM_CODE_PREFIX + compact
