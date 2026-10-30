"""REST action -> database commit -> WebSocket broadcast."""


def connect_all(lobby, live, players=()):
    host = lobby.host_socket(live)
    host.wait_for_type("LOBBY_STATE")
    sockets = []
    for player in players:
        sock = lobby.player_socket(live, player)
        sock.wait_for_type("LOBBY_STATE")
        sockets.append(sock)
    return host, sockets


def team_row(state, slot):
    return next(t for t in state["teams"] if t["slot_no"] == slot)


def test_player_joining_appears_without_refresh(make_lobby, live):
    lobby = make_lobby()
    alice = lobby.join("Alice")
    host, (asock,) = connect_all(lobby, live, [alice])

    bob = lobby.join("Bob")  # REST in another browser
    event = host.wait_for_type("PLAYER_JOINED")
    assert event["payload"]["display_name"] == "Bob" and event["payload"]["player_id"] == bob.id
    state = host.wait_for_state(lambda s: s["counts"]["player_count"] == 2)
    assert [p["display_name"] for p in state["players"]] == ["Alice", "Bob"]

    player_event = asock.wait_for_type("PLAYER_JOINED")
    assert player_event["payload"] == {"display_name": "Bob"}  # no row ids for players
    state = asock.wait_for_state(lambda s: s["counts"]["player_count"] == 2)
    assert all(p["player_id"] is None for p in state["players"])


def test_team_join_updates_counts_and_status_everywhere(make_lobby, live):
    lobby = make_lobby(min_players=3, max_players=5)
    players = lobby.join_many(4)
    lobby.start_formation()
    lobby.assign(1, players[0])
    code = lobby.team_code(1)
    lobby.join_team(players[1], code)  # 2 of 3 needed
    host, (p2sock, p3sock) = connect_all(lobby, live, [players[2], players[3]])

    assert lobby.join_team(players[2], code).status_code == 200  # reaches the minimum

    assert host.wait_for_type("TEAM_JOINED")["payload"]["team_name"] == lobby.team(1)["name"]
    assert host.wait_for_type("TEAM_UPDATED")
    for sock in (host, p3sock):
        state = sock.wait_for_state(lambda s: team_row(s, 1)["player_count"] == 3)
        assert team_row(state, 1)["status"] == "READY"  # decided by the server
    own = p2sock.wait_for_state(lambda s: s["you"]["team_id"] is not None)
    assert team_row(own, 1)["members"] and own["you"]["is_leader"] is False


def test_leader_assignment_is_broadcast(make_lobby, live):
    lobby = make_lobby()
    alice, bob = lobby.join("Alice"), lobby.join("Bob")
    lobby.start_formation()
    host, (asock, bsock) = connect_all(lobby, live, [alice, bob])

    assert lobby.assign(1, alice).status_code == 200

    event = host.wait_for_type("LEADER_ASSIGNED")
    assert event["payload"]["display_name"] == "Alice" and "team_id" in event["payload"]
    assert "team_id" not in bsock.wait_for_type("LEADER_ASSIGNED")["payload"]
    state = bsock.wait_for_state(lambda s: team_row(s, 1)["leader_name"] == "Alice")
    assert team_row(state, 1)["team_code"] is None  # Bob never receives the code
    mine = asock.wait_for_state(lambda s: s["you"]["is_leader"])
    assert mine["you"]["team_code"] == lobby.team_code(1)  # Alice does


def test_formation_start_and_lock_are_broadcast(make_lobby, live):
    lobby = make_lobby()
    lobby.join_many(9)
    host, (psock,) = connect_all(lobby, live, [lobby.players[0]])

    lobby.start_formation()
    assert host.wait_for_type("TEAM_FORMATION_STARTED")
    state = psock.wait_for_state(lambda s: s["lobby_state"] == "TEAM_FORMATION")
    assert state["chat"]["enabled"] is False and "closed" in state["chat"]["closed_message"].lower()

    for slot, idx in ((1, 0), (2, 3), (3, 6)):
        lobby.assign(slot, lobby.players[idx])
        code = lobby.team_code(slot)
        for member in lobby.players[idx + 1 : idx + 3]:
            lobby.join_team(member, code)
    assert lobby.lock().status_code == 200

    assert host.wait_for_type("TEAM_FORMATION_LOCKED")
    final_host = host.wait_for_state(lambda s: s["lobby_state"] == "READY_TO_START")
    assert final_host["host_actions"]["can_start_game"] is True
    final_player = psock.wait_for_state(lambda s: s["team_formation_locked"])
    assert final_player["lobby_state"] == "READY_TO_START"

    # enforced by the server even if someone tries anyway
    assert lobby.join_team(lobby.players[1], lobby.team_code(2)).status_code == 409


def test_websocket_state_matches_the_rest_state(make_lobby, live):
    lobby = make_lobby(min_players=3, max_players=5)
    players = lobby.join_many(3)
    lobby.start_formation()
    lobby.assign(1, players[0])
    host, (sock,) = connect_all(lobby, live, [players[1]])

    response = lobby.join_team(players[1], lobby.team_code(1))
    rest = response.json()
    pushed = sock.wait_for_state(lambda s: s["you"]["team_id"] is not None)

    def summary(state):
        return [(t["name"], t["player_count"], t["status"], t["leader_name"]) for t in state["teams"]]

    assert summary(pushed) == summary(rest)
    assert pushed["lobby_state"] == rest["lobby_state"] and pushed["you"] == rest["you"]
    # and the host's pushed view equals what the host would fetch
    host_state = host.wait_for_state(lambda s: team_row(s, 1)["player_count"] == 2)
    fetched = lobby.lobby()
    assert summary(host_state) == summary(fetched)
    assert [p["display_name"] for p in host_state["players"]] == [p["display_name"] for p in fetched["players"]]


def test_leave_kick_and_chat_are_broadcast(make_lobby, live):
    lobby = make_lobby()
    alice, bob, cara = lobby.join("Alice"), lobby.join("Bob"), lobby.join("Cara")
    host, (asock,) = connect_all(lobby, live, [alice])

    assert alice.client.post(lobby.url("/chat"), json={"body": "boo!"}).status_code == 201
    assert host.wait_for_type("CHAT_MESSAGE")["payload"] == {"display_name": "Alice"}
    state = host.wait_for_state(lambda s: len(s["chat"]["messages"]) == 1)
    assert state["chat"]["messages"][0]["body"] == "boo!"

    assert lobby.kick(bob).status_code == 200
    assert host.wait_for_type("PLAYER_KICKED")["payload"]["display_name"] == "Bob"
    assert cara.client.post(lobby.url("/leave")).status_code == 200
    assert host.wait_for_type("PLAYER_LEFT")["payload"]["display_name"] == "Cara"
    host.wait_for_state(lambda s: s["counts"]["player_count"] == 1)


def test_snapshot_versions_only_increase(make_lobby, live):
    lobby = make_lobby()
    lobby.join("Alice")
    host, _ = connect_all(lobby, live)
    versions = []
    for name in ("Bob", "Cara", "Dan"):
        lobby.join(name)
        message = host.wait_for(
            lambda m, n=name: m["type"] == "LOBBY_STATE"
            and any(p["display_name"] == n for p in m["payload"]["players"])
        )
        versions.append(message["version"])
    assert versions == sorted(versions) and len(set(versions)) == 3


def test_failed_actions_broadcast_nothing(make_lobby, live):
    lobby = make_lobby()
    alice = lobby.join("Alice")
    host, (asock,) = connect_all(lobby, live, [alice])
    host.collect(0.8)
    asock.collect(0.8)
    assert lobby.join_team(alice, "TEAM-ZZZZ").status_code == 409  # formation not started
    assert lobby.lock().status_code == 409
    host.expect_silence(0.6)
    asock.expect_silence(0.6)
