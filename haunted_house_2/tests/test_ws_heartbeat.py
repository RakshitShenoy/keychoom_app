import pytest
from fastapi.testclient import TestClient

from app.db.base import Base
from app.main import create_app


@pytest.fixture
def fast_app(settings):
    """Same app, but with a 1s ping interval and a 2s timeout so the tests stay quick."""
    fast = settings.model_copy(
        update={"ws_heartbeat_interval_seconds": 1, "ws_heartbeat_timeout_seconds": 2}
    )
    application = create_app(fast)
    Base.metadata.create_all(application.state.db.engine)
    return application


def player_in_game(application):
    host = TestClient(application)
    code = host.post("/api/games", json={"team_count": 3}).json()["game_code"]
    player = TestClient(application)
    player.post("/api/games/join", json={"game_code": code, "display_name": "Alice"})
    return code, player


def test_server_sends_pings_and_a_responsive_client_stays_connected(fast_app, live):
    code, player = player_in_game(fast_app)
    sock = live(player, f"/ws/games/{code}?role=player")
    sock.wait_for_type("LOBBY_STATE")
    for _ in range(4):  # ~4 seconds, well beyond the 2s timeout
        sock.wait_for_type("PING", timeout=3)
        sock.send({"type": "PONG"})
    sock.send({"type": "PING"})
    assert sock.wait_for_type("PONG")  # still alive


def test_silent_connection_is_closed_by_the_server(fast_app, live):
    code, player = player_in_game(fast_app)
    sock = live(player, f"/ws/games/{code}?role=player")
    sock.wait_for_type("LOBBY_STATE")
    # never answer the pings
    error = sock.wait_for_type("ERROR", timeout=8)
    assert error["code"] == "HEARTBEAT_TIMEOUT"
    sock.expect_closed()


def test_timed_out_player_is_marked_disconnected_but_stays_a_member(fast_app, live):
    code, player = player_in_game(fast_app)
    sock = live(player, f"/ws/games/{code}?role=player")
    sock.wait_for_type("LOBBY_STATE")
    sock.wait_for_type("ERROR", timeout=8)
    sock.expect_closed()
    me = player.get("/api/me").json()["player"]
    assert me is not None and me["display_name"] == "Alice"  # membership untouched
