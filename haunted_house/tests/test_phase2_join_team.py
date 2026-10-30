from fastapi.testclient import TestClient


def code_of(response):
    return response.json()["error"]["code"]


def test_valid_team_code_joins_the_team(make_lobby):
    lobby = make_lobby()
    alice, bob = lobby.join("Alice"), lobby.join("Bob")
    lobby.start_formation()
    lobby.assign(1, alice)

    response = lobby.join_team(bob, lobby.team_code(1))
    assert response.status_code == 200
    body = response.json()
    assert body["you"]["team_id"] == lobby.team(1)["team_id"]
    assert body["you"]["is_leader"] is False
    assert lobby.team(1)["player_count"] == 2
    assert sorted(lobby.team(1)["members"]) == ["Alice", "Bob"]


def test_team_code_is_forgiving_about_format(make_lobby):
    lobby = make_lobby()
    alice, bob, cara = lobby.join("Alice"), lobby.join("Bob"), lobby.join("Cara")
    lobby.start_formation()
    lobby.assign(1, alice)
    code = lobby.team_code(1)  # TEAM-XXXX
    assert lobby.join_team(bob, code.lower()).status_code == 200
    assert lobby.join_team(cara, code.split("-")[1]).status_code == 200


def test_wrong_team_code_fails(make_lobby):
    lobby = make_lobby()
    alice, bob = lobby.join("Alice"), lobby.join("Bob")
    lobby.start_formation()
    lobby.assign(1, alice)
    response = lobby.join_team(bob, "TEAM-ZZZZ")
    assert response.status_code == 404 and code_of(response) == "INVALID_TEAM_CODE"
    assert lobby.join_team(bob, "").status_code == 404
    assert lobby.lobby()["counts"]["unassigned_count"] == 1


def test_team_code_from_another_game_fails(make_lobby, second_host, harness):
    first = make_lobby()
    alice, bob = first.join("Alice"), first.join("Bob")
    first.start_formation()
    first.assign(1, alice)

    other_response = second_host.post("/api/games", json={"team_count": 3})
    other = harness(second_host, other_response.json())
    carol, dave = other.join("Carol"), other.join("Dave")
    other.start_formation()
    other.assign(1, carol)
    foreign_code = other.team_code(1)

    # A valid code of game 2 must not work inside game 1 ...
    response = first.join_team(bob, foreign_code)
    assert response.status_code == 404 and code_of(response) == "INVALID_TEAM_CODE"
    # ... and a valid code of game 1 must not work through game 2's URL.
    cross = dave.client.post(other.url("/teams/join"), json={"team_code": first.team_code(1)})
    assert cross.status_code == 404 and code_of(cross) == "INVALID_TEAM_CODE"


def test_full_team_rejects_players(make_lobby):
    lobby = make_lobby(min_players=3, max_players=3)
    players = lobby.join_many(5)
    lobby.start_formation()
    lobby.assign(1, players[0])
    code = lobby.team_code(1)
    assert lobby.join_team(players[1], code).status_code == 200
    assert lobby.join_team(players[2], code).status_code == 200
    assert lobby.team(1)["status"] == "FULL"

    over = lobby.join_team(players[3], code)
    assert over.status_code == 409 and code_of(over) == "TEAM_FULL"
    assert lobby.team(1)["player_count"] == 3


def test_player_cannot_join_two_teams_or_the_same_team_twice(make_lobby):
    lobby = make_lobby()
    alice, bob, cara = lobby.join("Alice"), lobby.join("Bob"), lobby.join("Cara")
    lobby.start_formation()
    lobby.assign(1, alice)
    lobby.assign(2, bob)
    assert lobby.join_team(cara, lobby.team_code(1)).status_code == 200

    other_team = lobby.join_team(cara, lobby.team_code(2))
    assert other_team.status_code == 409 and code_of(other_team) == "ALREADY_ON_TEAM"
    same_team = lobby.join_team(cara, lobby.team_code(1))
    assert same_team.status_code == 409
    assert lobby.team(2)["player_count"] == 1


def test_leader_cannot_also_join_another_team(make_lobby):
    lobby = make_lobby()
    alice, bob = lobby.join("Alice"), lobby.join("Bob")
    lobby.start_formation()
    lobby.assign(1, alice)
    lobby.assign(2, bob)
    response = lobby.join_team(alice, lobby.team_code(2))
    assert response.status_code == 409 and code_of(response) == "ALREADY_ON_TEAM"


def test_joining_before_formation_or_after_lock_is_rejected(make_lobby):
    lobby = make_lobby()
    alice = lobby.join("Alice")
    early = lobby.join_team(alice, "TEAM-AAAA")
    assert early.status_code == 409 and code_of(early) == "TEAM_FORMATION_NOT_STARTED"

    full = make_lobby()
    full.build_full_teams()
    code = full.team_code(1)
    assert full.lock().status_code == 200
    late = full.join_team(full.players[-1], code)
    assert late.status_code == 409 and code_of(late) == "TEAM_FORMATION_LOCKED"


def test_non_game_player_is_rejected(make_lobby):
    lobby = make_lobby()
    alice = lobby.join("Alice")
    lobby.start_formation()
    lobby.assign(1, alice)
    stranger = TestClient(lobby.app)  # no session at all
    response = stranger.post(lobby.url("/teams/join"), json={"team_code": lobby.team_code(1)})
    assert response.status_code == 401


def test_host_cannot_join_a_team(make_lobby):
    lobby = make_lobby()
    alice = lobby.join("Alice")
    lobby.start_formation()
    lobby.assign(1, alice)
    response = lobby.host.post(lobby.url("/teams/join"), json={"team_code": lobby.team_code(1)})
    assert response.status_code == 401  # the host has no player session


def test_guessing_team_codes_is_rate_limited(make_lobby):
    lobby = make_lobby()
    alice, bob = lobby.join("Alice"), lobby.join("Bob")
    lobby.start_formation()
    lobby.assign(1, alice)
    good_code = lobby.team_code(1)
    for _ in range(10):  # TEAM_CODE_FAILURE_LIMIT default
        assert lobby.join_team(bob, "TEAM-ZZZZ").status_code == 404
    blocked = lobby.join_team(bob, "TEAM-ZZZZ")
    assert blocked.status_code == 429
    assert lobby.join_team(bob, good_code).status_code == 429  # even the right code, while blocked
