"""Process-wide gameplay container (single worker): content, leases, per-team locks, cooldowns.

All state that must survive a restart lives in the database; this object only holds what is
short-lived by design (object leases and wrong-answer cooldowns).
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable

from app.game.leases import LeaseRegistry

if TYPE_CHECKING:
    from app.config import Settings
    from app.content.loader import ContentLibrary
    from app.lobby.events import LobbyEventBus


class GameRuntime:
    def __init__(
        self, settings: "Settings", bus: "LobbyEventBus", get_content: Callable[[], "ContentLibrary"]
    ) -> None:
        self.settings = settings
        self.bus = bus
        self._get_content = get_content
        self.leases = LeaseRegistry(settings.object_lock_lease_seconds)
        self._guard = threading.Lock()
        self._team_locks: dict[str, threading.RLock] = {}
        self._cooldowns: dict[tuple[str, str], datetime] = {}

    @property
    def content(self) -> "ContentLibrary":
        return self._get_content()

    def team_lock(self, team_id: str) -> threading.RLock:
        """One lock per team: every change to a team's game state happens inside it."""
        with self._guard:
            return self._team_locks.setdefault(team_id, threading.RLock())

    # -- wrong-answer cooldowns ------------------------------------------------------------
    def cooldown_remaining(self, team_id: str, key: str, now: datetime) -> float:
        with self._guard:
            until = self._cooldowns.get((team_id, key))
            if until is None or until <= now:
                self._cooldowns.pop((team_id, key), None)
                return 0.0
            return (until - now).total_seconds()

    def start_cooldown(self, team_id: str, key: str, now: datetime) -> None:
        seconds = self.settings.answer_cooldown_seconds
        if seconds > 0:
            with self._guard:
                self._cooldowns[(team_id, key)] = now + timedelta(seconds=seconds)

    def clear_cooldowns(self, team_id: str) -> None:
        with self._guard:
            for k in [k for k in self._cooldowns if k[0] == team_id]:
                del self._cooldowns[k]
