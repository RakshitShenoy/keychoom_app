"""The WebSocket endpoint: ``/ws/games/{game_code}`` (cookie-authenticated, one game per socket)."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from urllib.parse import urlparse

import anyio
from fastapi import APIRouter, WebSocket
from starlette.concurrency import run_in_threadpool

from app.auth.ratelimit import FailureLimiter
from app.errors import AppError
from app.ws.hub import KICKED_ERROR, NOT_AUTHORIZED_ERROR, RealtimeHub, SocketIdentity
from app.ws.manager import Connection
from app.ws.protocol import (
    CLOSE_FORBIDDEN, CLOSE_HEARTBEAT, CLOSE_UNAUTHENTICATED,
    dashboard_message, error_message, floor_state_message, ping_message, pong_message, state_message,
)

log = logging.getLogger(__name__)
router = APIRouter()


def _origin_allowed(websocket: WebSocket, settings) -> bool:
    """Cross-site WebSocket hijacking guard. Non-browser clients send no Origin header."""
    origin = websocket.headers.get("origin")
    if not origin or not settings.ws_check_origin:
        return True
    origin_host = urlparse(origin).netloc.lower()
    allowed = {
        websocket.headers.get("host", "").lower(),
        websocket.headers.get("x-forwarded-host", "").lower(),
        *settings.ws_extra_origins,
    }
    return origin_host in allowed


async def _refuse(websocket: WebSocket, code: str, message: str, close_code: int) -> None:
    try:
        await websocket.send_json(error_message(code, message))
        await websocket.close(code=close_code)
    except Exception:  # the peer may already be gone
        pass


async def _writer(websocket: WebSocket, conn: Connection) -> None:
    while True:
        item = await conn.queue.get()
        if item[0] == "msg":
            await websocket.send_json(item[1])
            continue
        _, close_code, error = item
        if error is not None:
            await websocket.send_json(error)
        await websocket.close(code=close_code)
        return


async def _send_state(hub: RealtimeHub, conn: Connection, identity: SocketIdentity) -> None:
    snapshot = await run_in_threadpool(
        hub.snapshot_for, identity.game_id, identity.role, identity.player_id
    )
    if snapshot is None:
        conn.close(CLOSE_FORBIDDEN, error_message(*NOT_AUTHORIZED_ERROR))
        return
    version, payload, team_id = snapshot
    conn.team_id = team_id
    conn.send(state_message(version, payload))
    if identity.role == "host":  # the host's whole-game overview (nobody else ever gets it)
        dashboard = await run_in_threadpool(hub.dashboard_for, identity.game_id)
        if dashboard is not None:
            conn.send(dashboard_message(*dashboard))
    if team_id is not None:  # Phase 4: the team's private floor view follows the lobby state
        floor = await run_in_threadpool(hub.floor_state_for, identity.game_id, identity.player_id)
        if floor is not None:
            conn.send(floor_state_message(hub.next_floor_version(team_id), floor))


async def _reader(
    websocket: WebSocket, conn: Connection, hub: RealtimeHub, identity: SocketIdentity,
    limiter: FailureLimiter,
) -> None:
    settings = hub.settings
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
        conn.last_seen = time.monotonic()
        raw = message.get("text")
        if raw is None:
            conn.send(error_message("INVALID_MESSAGE", "Only text messages are accepted."))
            continue
        if len(raw) > settings.ws_max_message_bytes:
            conn.send(error_message("MESSAGE_TOO_LARGE", "That message is too large."))
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            conn.send(error_message("INVALID_MESSAGE", "Messages must be valid JSON."))
            continue
        kind = data.get("type") if isinstance(data, dict) else None
        if not isinstance(kind, str):
            kind = None

        if kind == "PONG":
            continue
        if kind == "PING":
            conn.send(pong_message())
        elif kind == "REQUEST_STATE":
            if limiter.is_blocked(conn.id):
                conn.send(error_message("RATE_LIMITED", "Too many state requests."))
            else:
                limiter.record_failure(conn.id)  # counts every request (it caps the request rate)
                await _send_state(hub, conn, identity)
        else:
            # Anything else (IDENTIFY, ASSIGN_LEADER, JOIN_TEAM, ...) is refused: identity comes
            # from the session cookie and every state change goes through the REST API.
            conn.send(
                error_message(
                    "UNSUPPORTED_MESSAGE", "That message is not supported. Use the normal game actions."
                )
            )


async def _heartbeat(conn: Connection, settings) -> None:
    while True:
        await asyncio.sleep(settings.ws_heartbeat_interval_seconds)
        if time.monotonic() - conn.last_seen > settings.ws_heartbeat_timeout_seconds:
            conn.close(CLOSE_HEARTBEAT, error_message("HEARTBEAT_TIMEOUT", "Connection timed out."))
            # Do NOT return: the writer must first deliver the ERROR and close the socket.
            # Returning here would end asyncio.wait(FIRST_COMPLETED) and cancel the writer early.
            await asyncio.Event().wait()
        conn.send(ping_message())


@router.websocket("/ws/games/{game_code}")
async def game_socket(websocket: WebSocket, game_code: str) -> None:
    app = websocket.app
    settings = app.state.settings
    hub: RealtimeHub = app.state.realtime
    await websocket.accept()

    if not _origin_allowed(websocket, settings):
        await _refuse(websocket, *NOT_AUTHORIZED_ERROR, CLOSE_FORBIDDEN)
        return

    role = websocket.query_params.get("role")
    role = role if role in ("host", "player") else None
    try:
        identity = await run_in_threadpool(hub.authenticate, websocket.cookies, game_code, role)
    except AppError as exc:
        if exc.status_code == 401:
            await _refuse(
                websocket, "NOT_AUTHENTICATED", "You are not logged in to this game.",
                CLOSE_UNAUTHENTICATED,
            )
        else:
            await _refuse(websocket, *NOT_AUTHORIZED_ERROR, CLOSE_FORBIDDEN)
        return

    conn = Connection(
        identity.game_id, identity.role, identity.player_id,
        asyncio.get_running_loop(), settings.ws_send_queue_size,
    )
    first_for_player = hub.manager.add(conn)
    limiter = FailureLimiter(settings.ws_state_request_limit, settings.ws_state_request_window_seconds)
    tasks: list[asyncio.Task] = []
    try:
        # The writer starts BEFORE anything is queued: a registered connection whose writer is not
        # running yet cannot drain, so a burst of other players connecting used to overflow its queue
        # and get it dropped as TOO_SLOW before it had even received its first message.
        writer_task = asyncio.create_task(_writer(websocket, conn))
        tasks.append(writer_task)
        # Registered first, THEN the snapshot is read: nothing that happens in between is missed.
        await _send_state(hub, conn, identity)
        if first_for_player and identity.player_id:
            await run_in_threadpool(hub.set_presence, identity.game_id, identity.player_id, True)

        tasks += [
            asyncio.create_task(_reader(websocket, conn, hub, identity, limiter)),
            asyncio.create_task(_heartbeat(conn, settings)),
        ]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except Exception:
        log.exception("websocket handler failed")  # details stay in the server log only
    finally:
        # The whole clean-up is shielded: even if this handler is being cancelled (server shutdown,
        # a dropped connection torn down by the ASGI server) the presence flag must still be updated.
        with anyio.CancelScope(shield=True):
            conn.closed = True
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            last_for_player = hub.manager.remove(conn)
            if last_for_player and identity.player_id:
                try:
                    await run_in_threadpool(hub.set_presence, identity.game_id, identity.player_id, False)
                except Exception:
                    log.exception("presence update failed")
            try:
                await websocket.close()
            except Exception:
                pass
