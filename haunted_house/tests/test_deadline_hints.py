from datetime import datetime, timedelta, timezone

import pytest

from app.hints.penalties import penalty_for_hint, total_penalty
from app.timers.deadline import compute_deadline, seconds_remaining

START = datetime(2026, 10, 31, 20, 0, 0, tzinfo=timezone.utc)
SCHEDULE = (30, 60, 90, 120)


def test_deadline_formula():
    assert compute_deadline(START, 3600) == START + timedelta(hours=1)
    assert compute_deadline(START, 3600, 90) == START + timedelta(seconds=3690)


def test_deadline_rejects_naive_and_bad_values():
    with pytest.raises(ValueError):
        compute_deadline(datetime(2026, 1, 1), 3600)
    with pytest.raises(ValueError):
        compute_deadline(START, 0)
    with pytest.raises(ValueError):
        compute_deadline(START, 3600, -1)


def test_seconds_remaining_never_negative():
    deadline = START + timedelta(seconds=100)
    assert seconds_remaining(deadline, START) == 100
    assert seconds_remaining(deadline, START + timedelta(seconds=500)) == 0


def test_hint_penalties_follow_schedule():
    assert [penalty_for_hint(n, SCHEDULE) for n in (1, 2, 3, 4)] == [30, 60, 90, 120]
    assert [total_penalty(n, SCHEDULE) for n in range(5)] == [0, 30, 90, 180, 300]


def test_hint_bounds():
    with pytest.raises(ValueError):
        penalty_for_hint(0, SCHEDULE)
    with pytest.raises(ValueError):
        penalty_for_hint(5, SCHEDULE)
    with pytest.raises(ValueError):
        total_penalty(5, SCHEDULE)
