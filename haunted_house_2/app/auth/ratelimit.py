"""Tiny in-memory limiter for *failed* game-code guesses.

Only failures are counted so that many legitimate players sharing one venue
(one public IP) are never blocked. Single-process by design (see README).
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Callable


class FailureLimiter:
    def __init__(
        self, max_failures: int, window_seconds: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._max = max_failures
        self._window = window_seconds
        self._clock = clock
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        events = self._failures[key]
        while events and now - events[0] > self._window:
            events.popleft()
        return events

    def is_blocked(self, key: str) -> bool:
        with self._lock:
            now = self._clock()
            events = self._prune(key, now)
            if not events:
                self._failures.pop(key, None)
                return False
            return len(events) >= self._max

    def record_failure(self, key: str) -> None:
        with self._lock:
            now = self._clock()
            self._prune(key, now).append(now)
