"""Connect / disconnect / reconnect: presence is tracked separately from membership."""
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Player
from app.main import create_app


def is_connected(state, name):
    return next(p for p in state["players"] if p["display_name"] == name)["is_connected"]


def test_disconnect_does_not_remove_the_player_and_is_announced(make_lobby, live):
    lobby = make_lobby()
    alice, bob = lobby.join("Alice"), lobby.join("Bob")
    host = lobby.host_socket(live)
    host.wait_for_type("LOBBY_STATE")

    asock = lobby.player_socket(live, alice)
    connected = host.wait_for_type("PLAYER_CONNECTED")
    assert connected["payload"]["display_name"] == "Alice"
    host.wait_for_state(lambda s: is_connected(s, "Alice"))

    asock.close()
    assert host.wait_for_type("PLAYER_DISCONNECTED")["payload"]["display_name"] == "Alice"
    state = host.wait_for_state(lambda s: not is_connected(s, "Alice"))
    assert state["counts"]["player_count"] == 2  # still a member
    assert lobby.lobby()["counts"]["player_count"] == 2


def test_reconnect_resumes_the_same_player_with_current_state(make_lobby, live):
    lobby = make_lobby()
    lobby.build_full_teams()
    member = lobby.players[1]
    host = lobby.host_socket(live)
    host.wait_for_type("LOBBY_STATE")

    first = lobby.player_socket(live, member)
    before = first.wait_for_type("LOBBY_STATE")["payload"]
    first.close()
    host.wait_for_type("PLAYER_DISCONNECTED")

    lobby.lock()  # the world moves on while the player is offline

    second = lobby.player_socket(live, member)
    after = second.wait_for_type("LOBBY_STATE")["payload"]
    assert after["you"]["player_id"] == before["you"]["player_id"] == member.id
    assert after["you"]["team_id"] == before["you"]["team_id"]       # same team
    assert after["lobby_state"] == "READY_TO_START"                   # the server's state wins
    assert host.wait_for_type("PLAYER_CONNECTED")["payload"]["display_name"] == member.name
    assert lobby.lobby()["counts"]["player_count"] == 9               # no duplicate player rows


def test_two_tabs_of_one_player_count_as_one_presence(make_lobby, live):
    lobby = make_lobby()
    alice = lobby.join("Alice")
    host = lobby.host_socket(live)
    host.wait_for_type("LOBBY_STATE")
    tab1 = lobby.player_socket(live, alice)
    tab2 = lobby.player_socket(live, alice)
    tab1.wait_for_type("LOBBY_STATE")
    tab2.wait_for_type("LOBBY_STATE")
    host.wait_for_state(lambda s: is_connected(s, "Alice"))
    host.collect(0.5)

    tab1.close()
    host.expect_no_type("PLAYER_DISCONNECTED", seconds=1.0)
    assert lobby.lobby()["players"][0]["is_connected"] is True

    tab2.close()
    host.wait_for_type("PLAYER_DISCONNECTED")


def test_rest_membership_survives_a_dropped_connection(make_lobby, live):
    lobby = make_lobby()
    lobby.build_full_teams()
    member = lobby.players[2]
    sock = lobby.player_socket(live, member)
    sock.wait_for_type("LOBBY_STATE")
    sock.close()
    view = member.client.get(lobby.url("/lobby")).json()  # a refresh = same session, same player
    assert view["you"]["player_id"] == member.id and view["you"]["team_id"] is not None


def test_presence_flags_are_reset_when_the_server_restarts(make_lobby, settings):
    lobby = make_lobby()
    alice = lobby.join("Alice")
    first = create_app(settings)
    with TestClient(first):
        pass
    session = first.state.db.session_factory()
    session.get(Player, alice.id).is_connected = True  # a crash left a stale flag
    session.commit()
    session.close()

    restarted = create_app(settings)
    with TestClient(restarted):
        check = restarted.state.db.session_factory()
        flags = check.scalars(select(Player.is_connected)).all()
        check.close()
    assert flags == [False]
