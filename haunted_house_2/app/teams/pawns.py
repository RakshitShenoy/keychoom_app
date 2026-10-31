"""Pawn colour palette. Assigned randomly; per-team uniqueness arrives with team formation."""
from __future__ import annotations

import secrets

PAWN_COLORS: tuple[str, ...] = (
    "#e63946", "#f77f00", "#fcbf49", "#80ed99", "#2a9d8f", "#4cc9f0",
    "#4361ee", "#9d4edd", "#f72585", "#b5838d", "#ced4da", "#8d6e63",
)


def random_pawn_color() -> str:
    return secrets.choice(PAWN_COLORS)
