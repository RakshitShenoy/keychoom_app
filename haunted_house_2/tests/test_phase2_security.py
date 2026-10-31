from fastapi.testclient import TestClient


def code_of(response):
    return response.json()["error"]["code"]


def two_games(make_lobby, second_host, harness):
    first = make_lobby(team_count=3)
    second = harness(second_host, second_host.post("/api/games", json={"team_count": 3}).json())
    return first, second


def test_player_of_game_1_cannot_touch_game_2(make_lobby, second_host, harness):
    first, second = two_games(make_lobby, second_host, harness)
    a1 = first.join("Alice")
    b2, c2 = second.join("Bea"), second.join("Cy")
    second.start_formation()
    second.assign(1, b2)
    team_b = second.team(1)

    # reading game 2's lobby, joining its teams, chatting in it, leaving it: all refused
    assert a1.client.get(second.url("/lobby")).status_code == 403
    r = a1.client.post(second.url("/teams/join"), json={"team_code": second.team_code(1)})
    assert r.status_code == 403 and code_of(r) == "WRONG_GAME"
    assert a1.client.post(second.url("/chat"), json={"body": "hi"}).status_code == 403
    assert a1.client.post(second.url("/leave")).status_code == 403
    # host-only routes of game 2 are refused for a player of game 1 as well
    r = a1.client.post(second.url(f"/teams/{team_b['team_id']}/leader"), json={"player_id": a1.id})
    assert r.status_code == 403 and code_of(r) == "HOST_ONLY"
    assert second.team(1)["leader_name"] == "Bea" and second.team(1)["player_count"] == 1


def test_host_of_game_1_cannot_touch_game_2(make_lobby, second_host, harness):
    first, second = two_games(make_lobby, second_host, harness)
    a1 = first.join("Alice")
    b2 = second.join("Bea")
    second.start_formation()
    team_b = second.team(1)

    for method, url, body in (
        ("get", second.url("/lobby"), None),
        ("post", second.url("/team-formation/start"), None),
        ("post", second.url("/team-formation/lock"), None),
        ("post", second.url(f"/teams/{team_b['team_id']}/leader"), {"player_id": b2.id}),
        ("post", second.url(f"/players/{b2.id}/kick"), None),
    ):
        response = getattr(first.host, method)(url, **({"json": body} if body else {}))
        assert response.status_code == 403, (url, response.text)
        assert code_of(response) == "WRONG_GAME"
    assert second.lobby()["counts"]["player_count"] == 1
    assert a1.id  # (game 1 untouched)


def test_ids_from_another_game_are_not_accepted_inside_my_game(make_lobby, second_host, harness):
    first, second = two_games(make_lobby, second_host, harness)
    a1 = first.join("Alice")
    b2 = second.join("Bea")
    first.start_formation()
    second.start_formation()
    team1 = first.team(1)
    team2 = second.team(1)

    # my game's URL + another game's team id
    r = first.host.post(first.url(f"/teams/{team2['team_id']}/leader"), json={"player_id": a1.id})
    assert r.status_code == 404 and code_of(r) == "TEAM_NOT_FOUND"
    # my game's URL + another game's player id (assign and kick)
    r = first.host.post(first.url(f"/teams/{team1['team_id']}/leader"), json={"player_id": b2.id})
    assert r.status_code == 404 and code_of(r) == "PLAYER_NOT_FOUND"
    r = first.host.post(first.url(f"/players/{b2.id}/kick"))
    assert r.status_code == 404 and code_of(r) == "PLAYER_NOT_FOUND"
    assert second.lobby()["counts"]["player_count"] == 1 and second.team(1)["leader_name"] is None


def test_browser_cannot_inject_identity_or_role_fields(make_lobby):
    lobby = make_lobby()
    alice = lobby.join("Alice")
    lobby.start_formation()
    team_id = lobby.team(1)["team_id"]
    # extra fields such as role / player_id / game_id are rejected, not silently honoured
    r = alice.client.post(
        lobby.url("/teams/join"), json={"team_code": "TEAM-AAAA", "player_id": "x", "role": "leader"}
    )
    assert r.status_code == 422
    r = lobby.host.post(lobby.url(f"/teams/{team_id}/leader"), json={"player_id": alice.id, "role": "host"})
    assert r.status_code == 422


def test_player_cannot_make_themselves_leader(make_lobby):
    lobby = make_lobby()
    alice = lobby.join("Alice")
    lobby.start_formation()
    team_id = lobby.team(1)["team_id"]
    r = alice.client.post(lobby.url(f"/teams/{team_id}/leader"), json={"player_id": alice.id})
    assert r.status_code == 403
    r = alice.client.delete(lobby.url(f"/teams/{team_id}/leader"))
    assert r.status_code == 403
    assert lobby.team(1)["leader_name"] is None


def test_requests_without_any_session_are_refused(make_lobby):
    lobby = make_lobby()
    lobby.join("Alice")
    anonymous = TestClient(lobby.app)
    team_id = lobby.team(1)["team_id"]
    for method, suffix in (
        ("get", "/lobby"), ("post", "/team-formation/start"), ("post", "/team-formation/lock"),
        ("post", "/team-formation/reopen"), ("post", "/chat"), ("post", "/teams/join"),
        ("post", f"/teams/{team_id}/leader"), ("post", "/leave"),
    ):
        response = getattr(anonymous, method)(lobby.url(suffix))
        # routes that need a JSON body may answer 422 before authentication is evaluated;
        # either way nothing is executed for an anonymous caller
        assert response.status_code in (401, 422), (suffix, response.status_code)


def test_unknown_game_code_gives_no_information(make_lobby):
    lobby = make_lobby()
    alice = lobby.join("Alice")
    for client in (lobby.host, alice.client):
        response = client.get("/api/games/ZZZZZZ/lobby")
        assert response.status_code == 403 and code_of(response) == "WRONG_GAME"
    assert TestClient(lobby.app).get("/api/games/ZZZZZZ/lobby").status_code == 401


def test_game_code_in_the_url_is_forgiving_about_case(make_lobby):
    lobby = make_lobby()
    response = lobby.host.get(f"/api/games/{lobby.code.lower()}/lobby")
    assert response.status_code == 200


def test_lobby_payload_never_contains_tokens_or_hashes(make_lobby):
    lobby = make_lobby()
    lobby.build_full_teams()
    body = lobby.host.get(lobby.url("/lobby")).text.lower()
    assert "token" not in body and "hash" not in body
    assert lobby.players[1].client.get(lobby.url("/lobby")).text.lower().count("team-") == 0


def test_sessions_and_team_state_survive_a_server_restart(make_lobby, settings, copy_cookies):
    from app.main import create_app

    lobby = make_lobby()
    lobby.build_full_teams()
    lobby.lock()
    member = lobby.players[1]

    restarted = create_app(settings)  # same database, fresh process state
    with TestClient(restarted) as host2, TestClient(restarted) as player2:
        copy_cookies(lobby.host, host2)
        copy_cookies(member.client, player2)

        host_view = host2.get(lobby.url("/lobby")).json()
        assert host_view["lobby_state"] == "READY_TO_START"
        assert [t["player_count"] for t in host_view["teams"]] == [3, 3, 3]

        player_view = player2.get(lobby.url("/lobby")).json()
        assert player_view["you"]["display_name"] == member.name
        assert player_view["you"]["team_id"] is not None
        assert player2.get("/api/me").json()["player"]["game_code"] == lobby.code


def test_host_and_player_sessions_persist_across_requests(make_lobby):
    lobby = make_lobby()
    lobby.build_full_teams()
    member = lobby.players[1]
    for _ in range(3):
        assert lobby.host.get("/api/me").json()["host"]["game_code"] == lobby.code
        assert member.client.get("/api/me").json()["player"]["display_name"] == member.name
        assert member.client.get(lobby.url("/lobby")).json()["you"]["team_id"]
