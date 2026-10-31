"""WebSocket handshake, authentication and message hygiene."""
from fastapi.testclient import TestClient


def first_error(sock):
    message = sock.wait_for_type("ERROR")
    sock.expect_closed()
    return message


def test_host_receives_the_initial_state_on_connect(make_lobby, live):
    lobby = make_lobby()
    lobby.join_many(2)
    host = lobby.host_socket(live)
    message = host.wait_for_type("LOBBY_STATE")
    assert message["id"] and message["ts"] and isinstance(message["version"], int)
    state = message["payload"]
    assert state["game_code"] == lobby.code and state["you"]["role"] == "host"
    assert state["counts"]["player_count"] == 2 and state["host_actions"] is not None


def test_player_receives_their_own_state(make_lobby, live):
    lobby = make_lobby()
    alice, bob = lobby.join("Alice"), lobby.join("Bob")
    lobby.start_formation()
    lobby.assign(1, alice)
    sock = lobby.player_socket(live, bob)
    state = sock.wait_for_type("LOBBY_STATE")["payload"]
    assert state["you"]["role"] == "player" and state["you"]["player_id"] == bob.id
    assert state["host_actions"] is None
    assert all(t["team_code"] is None for t in state["teams"])  # Alice's code is not for Bob


def test_state_after_connecting_reflects_a_locked_game(make_lobby, live):
    lobby = make_lobby()
    lobby.build_full_teams()
    assert lobby.lock().status_code == 200
    member = lobby.players[1]
    state = lobby.player_socket(live, member).wait_for_type("LOBBY_STATE")["payload"]
    assert state["lobby_state"] == "READY_TO_START" and state["team_formation_locked"] is True
    assert state["you"]["team_id"] is not None


def test_connection_without_a_session_is_rejected(make_lobby, live):
    lobby = make_lobby()
    stranger = live(TestClient(lobby.app), f"/ws/games/{lobby.code}")
    error = first_error(stranger)
    assert error["code"] == "NOT_AUTHENTICATED"


def test_knowing_the_game_code_is_not_enough(make_lobby, live):
    lobby = make_lobby()
    lobby.join("Alice")
    stranger = live(TestClient(lobby.app), f"/ws/games/{lobby.code.lower()}?role=player")
    assert first_error(stranger)["code"] == "NOT_AUTHENTICATED"


def test_forged_cookie_is_rejected(make_lobby, live):
    lobby = make_lobby()
    forged = TestClient(lobby.app)
    forged.cookies.set("hh_player", "forged-token", domain="testserver.local", path="/")
    assert first_error(live(forged, f"/ws/games/{lobby.code}"))["code"] == "NOT_AUTHENTICATED"


def test_cross_game_access_is_rejected(make_lobby, second_host, harness, live):
    game_a = make_lobby()
    game_b = harness(second_host, second_host.post("/api/games", json={"team_count": 3}).json())
    alice = game_a.join("Alice")

    # a player of game A asks for game B
    sock = live(alice.client, f"/ws/games/{game_b.code}?role=player")
    assert first_error(sock)["code"] == "NOT_AUTHORIZED"
    # the host of game A asks for game B (and for an unknown game)
    assert first_error(live(game_a.host, f"/ws/games/{game_b.code}?role=host"))["code"] == "NOT_AUTHORIZED"
    assert first_error(live(game_a.host, "/ws/games/ZZZZZZ?role=host"))["code"] == "NOT_AUTHORIZED"


def test_a_player_cannot_claim_the_host_role(make_lobby, live):
    lobby = make_lobby()
    alice = lobby.join("Alice")
    sock = live(alice.client, f"/ws/games/{lobby.code}?role=host")
    assert first_error(sock)["code"] == "NOT_AUTHORIZED"


def test_identity_comes_from_the_cookie_not_from_the_request(make_lobby, live):
    lobby = make_lobby()
    alice, bob = lobby.join("Alice"), lobby.join("Bob")
    sock = live(alice.client, f"/ws/games/{lobby.code}?role=player&player_id={bob.id}")
    assert sock.wait_for_type("LOBBY_STATE")["payload"]["you"]["player_id"] == alice.id

    sock.send({"type": "IDENTIFY", "player_id": bob.id})
    assert sock.wait_for_type("ERROR")["code"] == "UNSUPPORTED_MESSAGE"
    sock.send({"type": "REQUEST_STATE", "player_id": bob.id})
    assert sock.wait_for_type("LOBBY_STATE")["payload"]["you"]["player_id"] == alice.id


def test_host_only_actions_cannot_be_sent_over_the_websocket(make_lobby, live):
    lobby = make_lobby()
    alice = lobby.join("Alice")
    sock = lobby.player_socket(live, alice)
    sock.wait_for_type("LOBBY_STATE")
    team_id = lobby.team(1)["team_id"]
    for attack in (
        {"type": "START_TEAM_FORMATION"},
        {"type": "LOCK_TEAM_FORMATION"},
        {"type": "ASSIGN_LEADER", "team_id": team_id, "player_id": alice.id},
        {"type": "KICK_PLAYER", "player_id": alice.id},
        {"type": "JOIN_TEAM", "team_code": "TEAM-AAAA"},
    ):
        sock.send(attack)
        assert sock.wait_for_type("ERROR")["code"] == "UNSUPPORTED_MESSAGE"
    assert lobby.lobby()["lobby_state"] == "WAITING"
    assert lobby.team(1)["leader_name"] is None


def test_foreign_origin_is_rejected_but_same_origin_is_accepted(make_lobby, live):
    lobby = make_lobby()
    evil = live(lobby.host, f"/ws/games/{lobby.code}?role=host", headers={"origin": "http://evil.example"})
    assert first_error(evil)["code"] == "NOT_AUTHORIZED"
    good = live(lobby.host, f"/ws/games/{lobby.code}?role=host", headers={"origin": "http://testserver"})
    assert good.wait_for_type("LOBBY_STATE")["payload"]["you"]["role"] == "host"


def test_malformed_messages_get_clean_errors(make_lobby, live):
    lobby = make_lobby()
    alice = lobby.join("Alice")
    sock = lobby.player_socket(live, alice)
    sock.wait_for_type("LOBBY_STATE")

    sock.send_text("this is not json")
    assert sock.wait_for_type("ERROR")["code"] == "INVALID_MESSAGE"
    sock.send_text("x" * 5000)
    assert sock.wait_for_type("ERROR")["code"] == "MESSAGE_TOO_LARGE"
    sock.send({"type": ["unhashable"]})
    assert sock.wait_for_type("ERROR")["code"] == "UNSUPPORTED_MESSAGE"
    sock.send_text("[1, 2, 3]")
    error = sock.wait_for_type("ERROR")
    assert error["code"] == "UNSUPPORTED_MESSAGE"
    # the socket survives bad input, and errors never leak internals
    for text in (error["message"],):
        for leak in ("Traceback", "sqlalchemy", ".py", "File ", "Error:"):
            assert leak not in text
    sock.send({"type": "PING"})
    assert sock.wait_for_type("PONG")


def test_request_state_returns_fresh_state_and_is_rate_limited(make_lobby, live):
    lobby = make_lobby()
    alice = lobby.join("Alice")
    sock = lobby.player_socket(live, alice)
    sock.wait_for_type("LOBBY_STATE")
    lobby.join("Bob")  # changes the state without the socket asking
    sock.send({"type": "REQUEST_STATE"})
    state = sock.wait_for_state(lambda s: s["counts"]["player_count"] == 2)
    assert state["you"]["player_id"] == alice.id
    for _ in range(8):
        sock.send({"type": "REQUEST_STATE"})
    assert sock.wait_for_type("ERROR")["code"] == "RATE_LIMITED"


def test_kicked_player_is_disconnected_and_cannot_reconnect(make_lobby, live):
    lobby = make_lobby()
    alice, bob = lobby.join("Alice"), lobby.join("Bob")
    sock = lobby.player_socket(live, alice)
    sock.wait_for_type("LOBBY_STATE")

    assert lobby.kick(alice).status_code == 200
    assert sock.wait_for_type("ERROR")["code"] == "KICKED"
    sock.expect_closed()

    # the old session cookie is invalid now
    again = live(alice.client, f"/ws/games/{lobby.code}?role=player")
    assert first_error(again)["code"] == "NOT_AUTHENTICATED"
    # Bob is unaffected
    assert lobby.player_socket(live, bob).wait_for_type("LOBBY_STATE")


def test_player_who_leaves_is_disconnected(make_lobby, live):
    lobby = make_lobby()
    alice = lobby.join("Alice")
    sock = lobby.player_socket(live, alice)
    sock.wait_for_type("LOBBY_STATE")
    assert alice.client.post(lobby.url("/leave")).status_code == 200
    sock.expect_closed()
