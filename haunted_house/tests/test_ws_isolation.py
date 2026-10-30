"""Two games running side by side must never see each other's events."""


def two_games(make_lobby, second_host, harness):
    game_a = make_lobby()
    game_b = harness(second_host, second_host.post("/api/games", json={"team_count": 3}).json())
    return game_a, game_b


def test_game_a_activity_never_reaches_game_b(make_lobby, second_host, harness, live):
    a, b = two_games(make_lobby, second_host, harness)
    a_players = a.join_many(6)
    b_players = b.join_many(3)

    host_a = a.host_socket(live)
    host_b = b.host_socket(live)
    sock_b = [b.player_socket(live, p) for p in b_players]
    sock_a = a.player_socket(live, a_players[1])
    for s in (host_a, host_b, sock_a, *sock_b):
        s.wait_for_type("LOBBY_STATE")
    for s in (host_a, host_b, sock_a, *sock_b):  # let connection chatter settle
        s.collect(0.8)

    # everything below happens in game A only
    a.start_formation()
    a.assign(1, a_players[0])
    a.join_team(a_players[1], a.team_code(1))
    a.join("Late Larry")
    a.kick(a_players[5])

    # game A's own clients do hear about it ...
    host_a.wait_for_type("TEAM_JOINED")
    sock_a.wait_for_state(lambda s: s["lobby_state"] == "TEAM_FORMATION")
    # ... game B hears absolutely nothing
    host_b.expect_silence(1.2)
    for s in sock_b:
        s.expect_silence(0.3)


def test_each_game_gets_its_own_state(make_lobby, second_host, harness, live):
    a, b = two_games(make_lobby, second_host, harness)
    a.join_many(2)
    b.join_many(4)
    host_a, host_b = a.host_socket(live), b.host_socket(live)
    state_a = host_a.wait_for_type("LOBBY_STATE")["payload"]
    state_b = host_b.wait_for_type("LOBBY_STATE")["payload"]
    assert state_a["game_code"] == a.code and state_a["counts"]["player_count"] == 2
    assert state_b["game_code"] == b.code and state_b["counts"]["player_count"] == 4


def test_team_scoped_broadcast_reaches_only_that_team(make_lobby, live):
    lobby = make_lobby()
    lobby.build_full_teams()
    team1 = lobby.team(1)["team_id"]
    team2_member = lobby.players[4]
    members = [lobby.player_socket(live, p) for p in lobby.players[0:3]]
    outsider = lobby.player_socket(live, team2_member)
    host = lobby.host_socket(live)
    for s in (*members, outsider, host):
        s.wait_for_type("LOBBY_STATE")
        s.collect(0.8)

    hub = lobby.app.state.realtime
    sent = hub.manager.send_team(lobby.game["game_id"], team1, {"type": "TEAM_ONLY", "payload": {}})
    assert sent == 3
    for s in members:
        assert s.wait_for_type("TEAM_ONLY")
    outsider.expect_silence(0.6)
    host.expect_silence(0.3)
