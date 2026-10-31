"""Hint penalty schedule helpers (schedule itself comes from configuration)."""
from __future__ import annotations

from typing import Sequence


def penalty_for_hint(hint_number: int, schedule: Sequence[int]) -> int:
    """Penalty in seconds for the Nth hint (1-based)."""
    if not 1 <= hint_number <= len(schedule):
        raise ValueError(f"hint_number must be between 1 and {len(schedule)}")
    return schedule[hint_number - 1]


def total_penalty(hints_used: int, schedule: Sequence[int]) -> int:
    """Accumulated penalty after ``hints_used`` hints."""
    if not 0 <= hints_used <= len(schedule):
        raise ValueError(f"hints_used must be between 0 and {len(schedule)}")
    return sum(schedule[:hints_used])
