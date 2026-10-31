def test_players_appear_for_host_with_ids_and_roles(make_lobby):
    lobby = make_lobby()
    lobby.join_many(4)
    view = lobby.lobby()
    assert view["counts"]["player_count"] == 4
    assert [p["display_name"] for p in view["players"]] == [f"Player {i}" for i in range(1, 5)]
    assert all(p["player_id"] and p["role"] == "UNASSIGNED" for p in view["players"])
    assert view["lobby_state"] == "WAITING" and view["you"]["role"] == "host"


def test_players_see_the_lobby_without_host_only_data(make_lobby):
    lobby = make_lobby()
    alice, bob = lobby.join("Alice"), lobby.join("Bob")
    lobby.start_formation()
    lobby.assign(1, alice)

    view = bob.client.get(lobby.url("/lobby")).json()
    assert view["you"]["role"] == "player" and view["you"]["display_name"] == "Bob"
    assert view["host_actions"] is None
    assert all(p["player_id"] is None and p["team_id"] is None for p in view["players"])
    assert all(t["leader_player_id"] is None and t["team_code"] is None for t in view["teams"])
    assert all(t["members"] == [] for t in view["teams"])  # not on any of those teams


def test_team_membership_and_counts_appear(make_lobby):
    lobby = make_lobby(team_count=3)
    lobby.build_full_teams(per_team=3)
    view = lobby.lobby()
    assert [t["player_count"] for t in view["teams"]] == [3, 3, 3]
    assert view["counts"]["unassigned_count"] == 0
    roles = [p["role"] for p in view["players"]]
    assert roles.count("LEADER") == 3 and roles.count("PLAYER") == 6
    assert all(p["team_name"] for p in view["players"])


def test_team_members_only_visible_to_their_own_team(make_lobby):
    lobby = make_lobby()
    lobby.build_full_teams()
    member_of_team_1 = lobby.players[1]
    view = member_of_team_1.client.get(lobby.url("/lobby")).json()
    mine = next(t for t in view["teams"] if t["team_id"] == view["you"]["team_id"])
    assert sorted(mine["members"]) == sorted(p.name for p in lobby.players[0:3])
    assert all(t["members"] == [] for t in view["teams"] if t["team_id"] != mine["team_id"])


def test_team_status_progression(make_lobby):
    lobby = make_lobby(min_players=3, max_players=5)
    players = lobby.join_many(5)
    lobby.start_formation()
    assert lobby.team(1)["status"] == "WAITING_FOR_LEADER"
    lobby.assign(1, players[0])
    assert lobby.team(1)["status"] == "WAITING_FOR_PLAYERS"
    code = lobby.team_code(1)
    lobby.join_team(players[1], code)
    assert lobby.team(1)["status"] == "WAITING_FOR_PLAYERS"
    lobby.join_team(players[2], code)
    assert lobby.team(1)["status"] == "READY" and lobby.team(1)["is_ready"]
    lobby.join_team(players[3], code)
    assert lobby.team(1)["status"] == "READY"
    lobby.join_team(players[4], code)
    assert lobby.team(1)["status"] == "FULL" and lobby.team(1)["is_ready"]


def test_game_readiness_is_calculated_by_the_server(make_lobby):
    lobby = make_lobby(team_count=3)
    lobby.join_many(9)
    lobby.start_formation()
    view = lobby.lobby()
    codes = {p["code"] for p in view["readiness"]["problems"]}
    assert not view["readiness"]["requirements_met"]
    assert {"TEAM_NO_LEADER", "TEAM_TOO_SMALL", "UNASSIGNED_PLAYERS"} <= codes
    assert view["host_actions"]["can_lock"] is False

    for slot, idx in ((1, 0), (2, 3), (3, 6)):
        lobby.assign(slot, lobby.players[idx])
        code = lobby.team_code(slot)
        for member in lobby.players[idx + 1 : idx + 3]:
            lobby.join_team(member, code)

    view = lobby.lobby()
    assert view["readiness"]["requirements_met"] and view["readiness"]["problems"] == []
    assert view["host_actions"]["can_lock"] is True
    assert view["readiness"]["ready_to_start"] is False  # not locked yet
    assert lobby.lock().status_code == 200
    final = lobby.lobby()
    assert final["lobby_state"] == "READY_TO_START" and final["readiness"]["ready_to_start"]
    assert final["host_actions"]["can_start_game"] is True


def test_capacity_and_totals_follow_the_configuration(make_lobby):
    lobby = make_lobby(team_count=4, min_players=3, max_players=5)
    counts = lobby.lobby()["counts"]
    assert (counts["min_total_players"], counts["max_total_players"], counts["capacity"]) == (12, 20, 20)
