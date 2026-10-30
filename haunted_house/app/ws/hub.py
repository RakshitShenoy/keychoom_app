"""Realtime hub: authentication, snapshots, presence and bus -> WebSocket fan-out.

Flow for every state change:   REST request -> validate -> DB commit -> LobbyEventBus ->
RealtimeHub.on_event -> (event message now) + (personalised LOBBY_STATE shortly after).
The database is always read AFTER the commit, so a snapshot can never show something that was
not saved. The WebSocket never changes state itself.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

from sqlalchemy import update

from app.auth.dependencies import identify_viewer
from app.db.models import Game, Player
from app.lobby.chat import recent_messages
from app.lobby.events import LobbyEvent, LobbyEventBus, LobbyEventType
from app.lobby.views import Viewer, build_lobby_view
from app.teams.queries import load_lobby_data
from app.ws.manager import ConnectionManager
from app.ws.protocol import (
    CLOSE_FORBIDDEN, CLOSE_NORMAL, dashboard_message, error_message, event_message, floor_state_message,
    public_payload, state_message,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import sessionmaker

    from app.config import Settings

log = logging.getLogger(__name__)

# Sent as individual messages to the host only. With N players connecting at once, announcing each
# one to every player is N*N messages; the players' own LOBBY_STATE already carries is_connected.
PRESENCE_EVENTS = frozenset({LobbyEventType.PLAYER_CONNECTED, LobbyEventType.PLAYER_DISCONNECTED})

KICKED_ERROR = ("KICKED", "You were removed from the game by the host.")
NOT_AUTHORIZED_ERROR = ("NOT_AUTHORIZED", "You are not authorized to access this game.")


@dataclass(frozen=True)
class SocketIdentity:
    role: str
    game_id: str
    player_id: str | None


class RealtimeHub:
    def __init__(
        self, settings: "Settings", session_factory: "sessionmaker", bus: LobbyEventBus
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.bus = bus
        self.manager = ConnectionManager()
        self._lock = threading.Lock()
        self._versions: dict[str, int] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._closed = False
        self._floor_versions: dict[str, int] = {}
        self._dash_versions: dict[str, int] = {}
        self.gameplay = None  # GameRuntime, attached by the application factory (Phase 4)
        bus.subscribe(self.on_event)

    # ------------------------------------------------------------------ authentication
    def authenticate(
        self, cookies: Mapping[str, str], game_code: str, role: str | None
    ) -> SocketIdentity:
        """Same rules as the REST API (``identify_viewer``). Raises AppError when refused."""
        db = self.session_factory()
        try:
            viewer = identify_viewer(db, self.settings, cookies, game_code, role)
            return SocketIdentity(
                role=viewer.role,
                game_id=viewer.game.id,
                player_id=viewer.player.id if viewer.player else None,
            )
        finally:
            db.close()

    # ------------------------------------------------------------------ snapshots
    def _next_version(self, game_id: str) -> int:
        with self._lock:
            self._versions[game_id] = self._versions.get(game_id, 0) + 1
            return self._versions[game_id]

    def snapshot_for(
        self, game_id: str, role: str, player_id: str | None
    ) -> tuple[int, dict[str, Any], str | None] | None:
        """Fresh authoritative state for ONE viewer, or None if they no longer belong here."""
        version = self._next_version(game_id)  # taken BEFORE reading: newer version => newer data
        db = self.session_factory()
        try:
            game = db.get(Game, game_id, populate_existing=True)
            if game is None:
                return None
            data = load_lobby_data(db, game)
            player = None
            if role == "player":
                player = next((p for p in data.players if p.id == player_id), None)
                if player is None:
                    return None
            view = build_lobby_view(
                db, self.settings, Viewer(role, game, player), data=data,
                messages=recent_messages(db, game, self.settings.chat_history_limit),
            )
            return version, view.model_dump(mode="json"), (player.team_id if player else None)
        finally:
            db.close()

    def push_snapshots(self, game_id: str) -> None:
        """Send every connected client of the game its own up-to-date LOBBY_STATE."""
        conns = self.manager.connections(game_id)
        if not conns:
            return
        version = self._next_version(game_id)
        db = self.session_factory()
        try:
            game = db.get(Game, game_id, populate_existing=True)
            if game is None:
                for conn in conns:
                    conn.close(CLOSE_FORBIDDEN, error_message(*NOT_AUTHORIZED_ERROR))
                return
            data = load_lobby_data(db, game)  # ONE load for all viewers
            messages = recent_messages(db, game, self.settings.chat_history_limit)
            players = {p.id: p for p in data.players}
            host_message = None
            player_messages: dict[str, dict] = {}
            for conn in conns:
                if conn.role == "host":
                    if host_message is None:
                        view = build_lobby_view(
                            db, self.settings, Viewer("host", game), data=data, messages=messages
                        )
                        host_message = state_message(version, view.model_dump(mode="json"))
                    conn.send(host_message)
                    continue
                player = players.get(conn.player_id)
                if player is None:  # kicked / left: this session no longer exists
                    conn.close(CLOSE_FORBIDDEN, error_message(*KICKED_ERROR))
                    continue
                conn.team_id = player.team_id
                if player.id not in player_messages:
                    view = build_lobby_view(
                        db, self.settings, Viewer("player", game, player), data=data, messages=messages
                    )
                    player_messages[player.id] = state_message(version, view.model_dump(mode="json"))
                conn.send(player_messages[player.id])
        finally:
            db.close()

    def _schedule_snapshot(self, game_id: str) -> None:
        """Coalesce bursts: many changes within the debounce window produce one push."""
        listeners = len(self.manager.connections(game_id))
        if not listeners:
            return  # nobody is listening: no need to read the database
        delay = max(
            self.settings.ws_snapshot_debounce_seconds,
            listeners * self.settings.ws_snapshot_cost_per_connection_seconds,
        )
        with self._lock:
            if self._closed or game_id in self._timers:
                return
            timer = threading.Timer(delay, self._flush, args=(game_id,))
            timer.daemon = True
            self._timers[game_id] = timer
        timer.start()

    def _flush(self, game_id: str) -> None:
        with self._lock:
            self._timers.pop(game_id, None)
        try:
            self.push_snapshots(game_id)
        except Exception:  # never let a push failure kill the timer thread silently
            log.exception("snapshot push failed for game %s", game_id)

    # ------------------------------------------------------------------ bus -> sockets
    def on_event(self, event: LobbyEvent) -> None:
        try:
            self._on_event(event)
        except Exception:
            log.exception("realtime fan-out failed for %s", event.type)

    # ------------------------------------------------------------------ floor state (Phase 4)
    def floor_state_for(self, game_id: str, player_id: str | None) -> dict[str, Any] | None:
        """A player's private floor state, or None (host, no team, or the game has not started)."""
        if self.gameplay is None or player_id is None:
            return None
        from app.game.views import build_floor_state

        db = self.session_factory()
        try:
            game = db.get(Game, game_id, populate_existing=True)
            player = db.get(Player, player_id, populate_existing=True)
            if game is None or player is None or player.game_id != game_id:
                return None
            if game.status not in ("RUNNING", "FINISHED"):
                return None
            return build_floor_state(db, self.gameplay, game, player)
        finally:
            db.close()

    def next_floor_version(self, team_id: str) -> int:
        with self._lock:
            self._floor_versions[team_id] = self._floor_versions.get(team_id, 0) + 1
            return self._floor_versions[team_id]

    def push_floor_snapshots(self, game_id: str, team_id: str) -> None:
        """Send every connected member of ONE team its own FLOOR_STATE (nobody else gets it)."""
        if self.gameplay is None:
            return
        from app.game.views import build_floor_state

        conns = [c for c in self.manager.connections(game_id) if c.role == "player" and c.team_id == team_id]
        if not conns:
            return
        version = self.next_floor_version(team_id)
        db = self.session_factory()
        try:
            game = db.get(Game, game_id, populate_existing=True)
            if game is None or game.status not in ("RUNNING", "FINISHED"):
                return
            players = {}
            for conn in conns:
                if conn.player_id not in players:
                    players[conn.player_id] = db.get(Player, conn.player_id, populate_existing=True)
                player = players[conn.player_id]
                if player is None or player.team_id != team_id:
                    continue
                state = build_floor_state(db, self.gameplay, game, player)
                if state is not None:
                    conn.send(floor_state_message(version, state))
        finally:
            db.close()

    def _schedule_floor_snapshot(self, game_id: str, team_id: str) -> None:
        key = f"floor:{team_id}"
        if not any(c.team_id == team_id for c in self.manager.connections(game_id)):
            return
        with self._lock:
            if self._closed or key in self._timers:
                return
            timer = threading.Timer(
                self.settings.ws_snapshot_debounce_seconds, self._flush_floor, args=(game_id, team_id, key)
            )
            timer.daemon = True
            self._timers[key] = timer
        timer.start()

    def _flush_floor(self, game_id: str, team_id: str, key: str) -> None:
        with self._lock:
            self._timers.pop(key, None)
        try:
            self.push_floor_snapshots(game_id, team_id)
        except Exception:
            log.exception("floor snapshot push failed for team %s", team_id)

    # ------------------------------------------------------------------ host dashboard (Phase 10)
    def dashboard_for(self, game_id: str) -> tuple[int, dict[str, Any]] | None:
        """The host's whole-game overview, or None before the game starts. Built from the database."""
        if self.gameplay is None:
            return None
        from app.game.views import build_host_dashboard

        with self._lock:
            self._dash_versions[game_id] = self._dash_versions.get(game_id, 0) + 1
            version = self._dash_versions[game_id]  # taken BEFORE reading: newer version => newer data
        db = self.session_factory()
        try:
            game = db.get(Game, game_id, populate_existing=True)
            if game is None or game.status not in ("RUNNING", "FINISHED"):
                return None
            return version, build_host_dashboard(db, self.gameplay, game)
        finally:
            db.close()

    def push_dashboard(self, game_id: str) -> None:
        """Send the dashboard to every HOST connection of the game (players never get it)."""
        hosts = [c for c in self.manager.connections(game_id) if c.role == "host"]
        if not hosts:
            return
        result = self.dashboard_for(game_id)
        if result is None:
            return
        message = dashboard_message(*result)
        for conn in hosts:
            conn.send(message)

    def _schedule_dashboard(self, game_id: str) -> None:
        if self.gameplay is None or not any(c.role == "host" for c in self.manager.connections(game_id)):
            return
        key = f"dash:{game_id}"
        with self._lock:
            if self._closed or key in self._timers:
                return
            timer = threading.Timer(self.settings.ws_dashboard_debounce_seconds, self._flush_dashboard, args=(game_id, key))
            timer.daemon = True
            self._timers[key] = timer
        timer.start()

    def _flush_dashboard(self, game_id: str, key: str) -> None:
        with self._lock:
            self._timers.pop(key, None)
        try:
            self.push_dashboard(game_id)
        except Exception:
            log.exception("dashboard push failed for game %s", game_id)

    def _on_team_event(self, event: LobbyEvent) -> None:
        """Gameplay events reach the host and the ONE team they belong to; nobody else."""
        if event.type is LobbyEventType.JUMPSCARE:
            # Presentation only: delivered to the team (or to the ONE player who sprang a trap) and the
            # host's feed; it never triggers a state push because it changes no state.
            only = event.payload.get("only_player_id")
            body = {k: v for k, v in event.payload.items() if k != "only_player_id"}
            self.manager.send_game(
                event.game_id, event_message(event.type.value, {**body, "team_id": event.team_id}), role="host"
            )
            if only:
                self.manager.send_player(event.game_id, only, event_message(event.type.value, body))
            else:
                self.manager.send_team(event.game_id, event.team_id, event_message(event.type.value, body))
            return
        self._schedule_dashboard(event.game_id)
        if event.type is not LobbyEventType.FLOOR_UPDATED:
            name = event.type.value
            self.manager.send_game(
                event.game_id, event_message(name, {**event.payload, "team_id": event.team_id}), role="host"
            )
            self.manager.send_team(event.game_id, event.team_id, event_message(name, dict(event.payload)))
        self._schedule_floor_snapshot(event.game_id, event.team_id)

    def _on_event(self, event: LobbyEvent) -> None:
        game_id = event.game_id
        if event.team_id is not None:
            self._on_team_event(event)
            return
        if event.type is LobbyEventType.LOBBY_UPDATED:
            self._schedule_snapshot(game_id)
            self._schedule_dashboard(game_id)  # presence, pause, winner... all arrive as "state changed"
            return

        name = event.type.value
        # Hosts get the full payload; players get it without row ids.
        self.manager.send_game(game_id, event_message(name, dict(event.payload)), role="host")
        if event.type in PRESENCE_EVENTS:
            return  # "X connected" is host information; players see presence in their snapshots
        self.manager.send_game(
            game_id, event_message(name, public_payload(event.payload)), role="player"
        )

        player_id = event.payload.get("player_id")
        if player_id and event.type is LobbyEventType.PLAYER_KICKED:
            self.manager.close_player(
                game_id, player_id, CLOSE_FORBIDDEN, error_message(*KICKED_ERROR)
            )
        elif player_id and event.type is LobbyEventType.PLAYER_LEFT:
            self.manager.close_player(game_id, player_id, CLOSE_NORMAL)

    # ------------------------------------------------------------------ presence
    def set_presence(self, game_id: str, player_id: str, connected: bool) -> None:
        """Record connected/disconnected (membership is NEVER touched) and announce it."""
        db = self.session_factory()
        try:
            player = db.get(Player, player_id, populate_existing=True)
            if player is None or player.game_id != game_id:
                return  # e.g. kicked in the meantime
            player.is_connected = connected
            name = player.display_name
            db.commit()
        finally:
            db.close()
        kind = LobbyEventType.PLAYER_CONNECTED if connected else LobbyEventType.PLAYER_DISCONNECTED
        self.bus.publish_change(
            LobbyEvent(kind, game_id, {"player_id": player_id, "display_name": name})
        )

    def reset_presence(self) -> None:
        """After a restart no browser is connected: clear stale flags."""
        db = self.session_factory()
        try:
            db.execute(update(Player).where(Player.is_connected.is_(True)).values(is_connected=False))
            db.commit()
        finally:
            db.close()

    def start(self) -> None:
        with self._lock:
            self._closed = False

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            timers = list(self._timers.values())
            self._timers.clear()
        for timer in timers:
            timer.cancel()
