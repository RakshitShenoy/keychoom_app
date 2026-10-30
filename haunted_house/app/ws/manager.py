"""Connection registry. No FastAPI imports: pure Python, thread-safe, easy to unit test.

Every browser connection is registered under its game, so a broadcast can only ever reach the
connections of ONE game (isolation), optionally narrowed to the host, the players, one team or one
player (the foundation for team-only gameplay messages later).

Messages are delivered through a per-connection queue that the connection's own event loop drains,
so ordering is preserved and the REST threads that publish events never block on a slow browser.
"""
from __future__ import annotations

import asyncio
import secrets
import threading
import time
from typing import Any

from app.ws.protocol import CLOSE_TOO_SLOW, error_message


class Connection:
    def __init__(
        self, game_id: str, role: str, player_id: str | None, loop: Any, queue_size: int = 200
    ) -> None:
        self.id = secrets.token_hex(8)
        self.game_id = game_id
        self.role = role  # "host" | "player"
        self.player_id = player_id
        self.team_id: str | None = None  # refreshed with every state push
        self.loop = loop
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=queue_size)
        self.last_seen = time.monotonic()
        self.closed = False

    # -- thread-safe producers (called from REST worker threads or the event loop) -----------
    def send(self, message: dict[str, Any]) -> bool:
        return self._post(("msg", message))

    def close(self, code: int, error: dict[str, Any] | None = None) -> bool:
        return self._post(("close", code, error))

    def _post(self, item: tuple) -> bool:
        if self.closed:
            return False
        try:
            self.loop.call_soon_threadsafe(self._put, item)
            return True
        except RuntimeError:  # the connection's event loop is gone
            self.closed = True
            return False

    def _put(self, item: tuple) -> None:
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            # Slow consumer: drop the backlog and disconnect it; it will reconnect and resync.
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait(
                ("close", CLOSE_TOO_SLOW, error_message("TOO_SLOW", "Connection too slow."))
            )


class ConnectionManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._games: dict[str, dict[str, Connection]] = {}

    # -- registry -------------------------------------------------------------------------------
    def add(self, conn: Connection) -> bool:
        """Register. Returns True if this is the player's first live connection."""
        with self._lock:
            game = self._games.setdefault(conn.game_id, {})
            already = conn.player_id is not None and any(
                c.player_id == conn.player_id for c in game.values()
            )
            game[conn.id] = conn
            return conn.player_id is not None and not already

    def remove(self, conn: Connection) -> bool:
        """Unregister. Returns True if that was the player's last live connection."""
        with self._lock:
            game = self._games.get(conn.game_id)
            if game is None or conn.id not in game:
                return False
            del game[conn.id]
            still_connected = conn.player_id is not None and any(
                c.player_id == conn.player_id for c in game.values()
            )
            if not game:
                del self._games[conn.game_id]
            return conn.player_id is not None and not still_connected

    def connections(self, game_id: str) -> list[Connection]:
        with self._lock:
            return list(self._games.get(game_id, {}).values())

    def count(self, game_id: str | None = None) -> int:
        with self._lock:
            if game_id is not None:
                return len(self._games.get(game_id, {}))
            return sum(len(g) for g in self._games.values())

    def is_player_connected(self, game_id: str, player_id: str) -> bool:
        return any(c.player_id == player_id for c in self.connections(game_id))

    # -- scoped delivery (every method is limited to ONE game) ------------------------------------
    def send_game(self, game_id: str, message: dict[str, Any], *, role: str | None = None) -> int:
        """Send to every connection of the game, optionally only hosts or only players."""
        sent = 0
        for conn in self.connections(game_id):
            if role is None or conn.role == role:
                sent += conn.send(message)
        return sent

    def send_team(self, game_id: str, team_id: str, message: dict[str, Any]) -> int:
        """Send only to the players currently on that team (host excluded)."""
        sent = 0
        for conn in self.connections(game_id):
            if conn.role == "player" and conn.team_id == team_id:
                sent += conn.send(message)
        return sent

    def send_player(self, game_id: str, player_id: str, message: dict[str, Any]) -> int:
        return sum(c.send(message) for c in self.connections(game_id) if c.player_id == player_id)

    def close_player(
        self, game_id: str, player_id: str, code: int, error: dict[str, Any] | None = None
    ) -> int:
        return sum(
            c.close(code, error) for c in self.connections(game_id) if c.player_id == player_id
        )
