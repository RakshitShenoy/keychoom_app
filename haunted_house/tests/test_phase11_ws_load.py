"""Regression tests for the TOO_SLOW drops found by tools/multiplayer_check.py at 250 players."""
import pytest

from app.ws.manager import Connection


@pytest.fixture
def settings(settings):
    # a tiny send queue makes the old bug (presence events for every player flooding every queue)
    # show up with a few dozen players instead of 250
    return settings.model_copy(update={"ws_send_queue_size": 12})


def test_many_players_connecting_at_once_do_not_overflow_each_others_queues(make_lobby, live):
    lobby = make_lobby(team_count=3, min_players=3, max_players=10)
    players = lobby.join_many(30)
    host = lobby.host_socket(live)
    host.wait_for_type("LOBBY_STATE")
    sockets = [lobby.player_socket(live, p) for p in players]  # 30 connections, one after another
    for sock in sockets:
        sock.wait_for_type("LOBBY_STATE")
    host.wait_for_state(lambda s: sum(1 for p in s["players"] if p["is_connected"]) == 30)
    for sock in sockets:
        seen = [m for m in sock.collect(0.3) if m is not sock.CLOSED]
        assert [m for m in seen if m["type"] == "ERROR"] == []  # nobody was dropped as TOO_SLOW
        # players do not receive a "connected" message about every other player (N*N messages)
        assert [m for m in seen if m["type"] == "PLAYER_CONNECTED"] == []


def test_host_still_hears_presence_events(make_lobby, live):
    lobby = make_lobby()
    alice = lobby.join("Alice")
    host = lobby.host_socket(live)
    host.wait_for_type("LOBBY_STATE")
    lobby.player_socket(live, alice)
    assert host.wait_for_type("PLAYER_CONNECTED")["payload"]["display_name"] == "Alice"


def test_a_connection_that_really_cannot_keep_up_is_still_dropped():
    """The slow-consumer protection is unchanged: a full queue ends in a TOO_SLOW close."""
    class Loop:
        def call_soon_threadsafe(self, fn, *a):
            fn(*a)

    conn = Connection("g", "player", "p", Loop(), 5)
    for i in range(6):
        conn.send({"type": "X", "n": i})
    kind, code, error = conn.queue.get_nowait()
    assert kind == "close" and code == 4429 and error["code"] == "TOO_SLOW"
    assert conn.queue.empty()
