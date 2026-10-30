"""Game-code generation.

Codes are short, easy to read aloud, random (never sequential) and avoid the
look-alike characters 0/O, 1/I (and L).
"""
from __future__ import annotations

import secrets
from typing import Callable

# 23 letters (no I, L, O) + digits 2-9 = 31 symbols. 31^6 is roughly 887 million codes.
GAME_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


class GameCodeGenerationError(RuntimeError):
    pass


def generate_game_code(length: int = 6) -> str:
    if length < 1:
        raise ValueError("length must be positive")
    return "".join(secrets.choice(GAME_CODE_ALPHABET) for _ in range(length))


def generate_unique_game_code(
    is_taken: Callable[[str], bool], length: int = 6, max_attempts: int = 25
) -> str:
    for _ in range(max_attempts):
        code = generate_game_code(length)
        if not is_taken(code):
            return code
    raise GameCodeGenerationError("Could not generate a unique game code")


def normalize_game_code(raw: str) -> str:
    """Forgiving input handling: ignore case, spaces and dashes (e.g. 'k7m-2qx')."""
    if not isinstance(raw, str):
        return ""
    return "".join(ch for ch in raw.upper() if ch not in " -\t\n")


def is_valid_game_code_format(code: str, length: int = 6) -> bool:
    return len(code) == length and all(ch in GAME_CODE_ALPHABET for ch in code)
