"""WebSocket message formats. ONE envelope for everything the server sends:

    {"type": "TEAM_JOINED", "id": "a1b2c3d4e5f6", "ts": "2026-10-31T20:00:00+00:00", "payload": {...}}
    {"type": "LOBBY_STATE", "id": "...", "ts": "...", "version": 12, "payload": {<full lobby view>}}
    {"type": "ERROR", "id": "...", "ts": "...", "code": "NOT_AUTHORIZED", "message": "..."}

The browser may only send PING, PONG and REQUEST_STATE. Every state change goes through the REST
API (validated and committed by the server first); the WebSocket only *announces* committed changes.
"""
from __future__ import annotations

import secrets
from typing import Any

from app.timers.clock import utcnow

# Message types that are not lobby events
LOBBY_STATE = "LOBBY_STATE"
FLOOR_STATE = "FLOOR_STATE"  # a team's private floor view (Phase 4)
HOST_DASHBOARD = "HOST_DASHBOARD"  # the whole-game overview, sent to HOST connections only (Phase 10)
ERROR = "ERROR"
PING = "PING"
PONG = "PONG"

CLIENT_MESSAGE_TYPES = frozenset({PING, PONG, "REQUEST_STATE"})

# Payload keys that identify rows. Only the host receives them; players never see other
# players' ids (the server resolves identities from the session cookie, never from messages).
PRIVATE_PAYLOAD_KEYS = frozenset({"player_id", "team_id"})

# WebSocket close codes (4000-4999 are reserved for applications)
CLOSE_NORMAL = 1000
CLOSE_UNAUTHENTICATED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_HEARTBEAT = 4408
CLOSE_TOO_SLOW = 4429

# Error codes that tell the browser NOT to reconnect
FATAL_ERROR_CODES = frozenset({"NOT_AUTHENTICATED", "NOT_AUTHORIZED", "KICKED"})


def _envelope(message_type: str, **fields: Any) -> dict[str, Any]:
    return {"type": message_type, "id": secrets.token_hex(6), "ts": utcnow().isoformat(), **fields}


def event_message(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _envelope(event_type, payload=payload)


def state_message(version: int, lobby: dict[str, Any]) -> dict[str, Any]:
    return _envelope(LOBBY_STATE, version=version, payload=lobby)


def floor_state_message(version: int, state: dict[str, Any]) -> dict[str, Any]:
    return _envelope(FLOOR_STATE, version=version, payload=state)


def dashboard_message(version: int, dashboard: dict[str, Any]) -> dict[str, Any]:
    return _envelope(HOST_DASHBOARD, version=version, payload=dashboard)


def error_message(code: str, message: str) -> dict[str, Any]:
    return _envelope(ERROR, code=code, message=message)


def ping_message() -> dict[str, Any]:
    return _envelope(PING)


def pong_message() -> dict[str, Any]:
    return _envelope(PONG)


def public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if k not in PRIVATE_PAYLOAD_KEYS}

