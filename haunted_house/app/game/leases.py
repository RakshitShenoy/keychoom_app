"""In-memory object leases: at most ONE player per team investigates an object at a time.

A lease is short (``OBJECT_LOCK_LEASE_SECONDS``) and renewed by a heartbeat while the player is
actually working on the object. Closed tabs and dropped connections therefore free the object by
themselves when the lease runs out (the sweeper collects expired leases). Leases are deliberately NOT
persisted: after a server restart every object is simply available again, and solved objects stay
solved because that lives in the database.

Every method takes ``now`` explicitly so tests never have to sleep. The registry is thread-safe; the
gameplay service additionally serialises each team's changes with a per-team lock.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Lease:
    team_id: str
    floor_no: int
    object_id: str
    player_id: str
    acquired_at: datetime
    expires_at: datetime


class LeaseRegistry:
    def __init__(self, ttl_seconds: float) -> None:
        self.ttl = timedelta(seconds=ttl_seconds)
        self._lock = threading.Lock()
        self._leases: dict[tuple[str, str], Lease] = {}  # (team_id, object_id) -> lease

    def _live(self, key: tuple[str, str], now: datetime) -> Lease | None:
        lease = self._leases.get(key)
        if lease is not None and lease.expires_at <= now:
            del self._leases[key]  # lazily drop an expired lease
            return None
        return lease

    def holder(self, team_id: str, object_id: str, now: datetime) -> Lease | None:
        with self._lock:
            return self._live((team_id, object_id), now)

    def acquire(
        self, team_id: str, floor_no: int, object_id: str, player_id: str, now: datetime
    ) -> tuple[str, Lease]:
        """Returns ``("new" | "renewed" | "busy", lease)``; for ``busy`` the lease is the holder's."""
        with self._lock:
            key = (team_id, object_id)
            current = self._live(key, now)
            if current is not None and current.player_id != player_id:
                return "busy", current
            lease = Lease(team_id, floor_no, object_id, player_id,
                          current.acquired_at if current else now, now + self.ttl)
            self._leases[key] = lease
            return ("renewed" if current else "new"), lease

    def renew(self, team_id: str, object_id: str, player_id: str, now: datetime) -> Lease | None:
        with self._lock:
            key = (team_id, object_id)
            current = self._live(key, now)
            if current is None or current.player_id != player_id:
                return None
            lease = Lease(current.team_id, current.floor_no, object_id, player_id,
                          current.acquired_at, now + self.ttl)
            self._leases[key] = lease
            return lease

    def release(self, team_id: str, object_id: str, player_id: str | None = None) -> Lease | None:
        """Drop the lease (only the holder's when ``player_id`` is given). Returns what was dropped."""
        with self._lock:
            key = (team_id, object_id)
            current = self._leases.get(key)
            if current is None or (player_id is not None and current.player_id != player_id):
                return None
            del self._leases[key]
            return current

    def lease_of_player(self, team_id: str, player_id: str, now: datetime) -> Lease | None:
        with self._lock:
            for key in list(self._leases):
                if key[0] == team_id and self._live(key, now) is not None and self._leases[key].player_id == player_id:
                    return self._leases[key]
            return None

    def active_for_team(self, team_id: str, now: datetime) -> list[Lease]:
        with self._lock:
            return [
                lease for key in list(self._leases) if key[0] == team_id
                and (lease := self._live(key, now)) is not None
            ]

    def clear_team(self, team_id: str) -> list[Lease]:
        with self._lock:
            keys = [k for k in self._leases if k[0] == team_id]
            return [self._leases.pop(k) for k in keys]

    def expire(self, now: datetime, skip_teams: frozenset[str] | set[str] = frozenset()) -> list[Lease]:
        """Remove and return every lease that has run out (teams in ``skip_teams`` are paused)."""
        with self._lock:
            dead = [
                k for k, lease in self._leases.items()
                if lease.expires_at <= now and lease.team_id not in skip_teams
            ]
            return [self._leases.pop(k) for k in dead]

    def release_player(self, player_id: str) -> list[Lease]:
        """Drop every lease a player holds (the host removed them from the game)."""
        with self._lock:
            keys = [k for k, lease in self._leases.items() if lease.player_id == player_id]
            return [self._leases.pop(k) for k in keys]

    def shift_teams(self, team_ids: set[str] | frozenset[str], delta: timedelta) -> int:
        """After a pause: push the expiry of these teams' leases forward by the pause length."""
        with self._lock:
            count = 0
            for key, lease in list(self._leases.items()):
                if lease.team_id in team_ids:
                    self._leases[key] = Lease(
                        lease.team_id, lease.floor_no, lease.object_id, lease.player_id,
                        lease.acquired_at, lease.expires_at + delta,
                    )
                    count += 1
            return count
