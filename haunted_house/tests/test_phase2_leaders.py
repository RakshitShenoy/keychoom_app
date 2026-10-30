from sqlalchemy import select

from app.db.models import Team

import re

TEAM_CODE_RE = re.compile(r"^TEAM-[A-HJ-KM-NP-Z2-9]{4}$")


def code_of(response):
    return response.json()["error"]["code"]


def test_host_can_assign_leader_and_team_gets_a_code(make_lobby):
    lobby = make_lobby()
    alice = lobby.join("Alice")
    lobby.start_formation()

    response = lobby.assign(1, alice)
    assert response.status_code == 200
    team = next(t for t in response.json()["teams"] if t["slot_no"] == 1)
    assert team["leader_name"] == "Alice"
    assert TEAM_CODE_RE.match(team["team_code"])
    assert team["player_count"] == 1 and team["status"] == "WAITING_FOR_PLAYERS"

    me = next(p for p in response.json()["players"] if p["display_name"] == "Alice")
    assert me["role"] == "LEADER" and me["team_name"] == team["name"]


def test_leader_sees_their_own_code_other_players_do_not(make_lobby):
    lobby = make_lobby()
    alice, bob = lobby.join("Alice"), lobby.join("Bob")
    lobby.start_formation()
    lobby.assign(1, alice)
    code = lobby.team_code(1)

    leader_view = alice.client.get(lobby.url("/lobby")).json()
    assert leader_view["you"]["is_leader"] and leader_view["you"]["team_code"] == code
    other_view = bob.client.get(lobby.url("/lobby")).json()
    assert other_view["you"]["team_code"] is None
    assert all(t["team_code"] is None for t in other_view["teams"])


def test_team_codes_are_unique_per_team(make_lobby):
    lobby = make_lobby(team_count=5)
    players = lobby.join_many(5)
    lobby.start_formation()
    for slot, player in enumerate(players, start=1):
        assert lobby.assign(slot, player).status_code == 200
    codes = [lobby.team_code(slot) for slot in range(1, 6)]
    assert len(set(codes)) == 5


def test_only_the_host_can_assign_leaders(make_lobby):
    lobby = make_lobby()
    alice = lobby.join("Alice")
    lobby.start_formation()
    team_id = lobby.team(1)["team_id"]
    url = lobby.url(f"/teams/{team_id}/leader")

    by_player = alice.client.post(url, json={"player_id": alice.id})  # self-promotion attempt
    assert by_player.status_code == 403 and code_of(by_player) == "HOST_ONLY"

    from fastapi.testclient import TestClient

    anonymous = TestClient(lobby.app).post(url, json={"player_id": alice.id})
    assert anonymous.status_code == 401
    assert lobby.team(1)["leader_name"] is None


def test_player_cannot_lead_two_teams(make_lobby):
    lobby = make_lobby()
    alice = lobby.join("Alice")
    lobby.start_formation()
    assert lobby.assign(1, alice).status_code == 200
    second = lobby.assign(2, alice)
    assert second.status_code == 409 and code_of(second) == "PLAYER_ALREADY_LEADER"


def test_team_cannot_have_two_leaders(make_lobby):
    lobby = make_lobby()
    alice, bob = lobby.join("Alice"), lobby.join("Bob")
    lobby.start_formation()
    assert lobby.assign(1, alice).status_code == 200
    second = lobby.assign(1, bob)
    assert second.status_code == 409 and code_of(second) == "TEAM_ALREADY_HAS_LEADER"
    assert lobby.team(1)["leader_name"] == "Alice"


def test_assigning_the_same_leader_again_is_harmless(make_lobby, db):
    lobby = make_lobby()
    alice = lobby.join("Alice")
    lobby.start_formation()
    lobby.assign(1, alice)
    code = lobby.team_code(1)
    assert lobby.assign(1, alice).status_code == 200  # page refresh / double click
    assert lobby.team_code(1) == code


def test_unknown_player_and_team_are_rejected(make_lobby):
    lobby = make_lobby()
    alice = lobby.join("Alice")
    lobby.start_formation()
    team_id = lobby.team(1)["team_id"]

    ghost = lobby.host.post(lobby.url(f"/teams/{team_id}/leader"), json={"player_id": "f" * 32})
    assert ghost.status_code == 404 and code_of(ghost) == "PLAYER_NOT_FOUND"
    bad_team = lobby.host.post(lobby.url("/teams/" + "0" * 32 + "/leader"), json={"player_id": alice.id})
    assert bad_team.status_code == 404 and code_of(bad_team) == "TEAM_NOT_FOUND"


def test_leaders_cannot_be_assigned_before_formation_or_after_lock(make_lobby):
    lobby = make_lobby()
    alice = lobby.join("Alice")
    early = lobby.assign(1, alice)
    assert early.status_code == 409 and code_of(early) == "TEAM_FORMATION_NOT_STARTED"

    lobby = make_lobby()
    lobby.build_full_teams()
    assert lobby.lock().status_code == 200
    extra = lobby.players[0]
    late = lobby.assign(1, extra)
    assert late.status_code == 409 and code_of(late) == "TEAM_FORMATION_LOCKED"


def test_player_on_another_team_cannot_be_made_leader_elsewhere(make_lobby):
    lobby = make_lobby()
    alice, bob = lobby.join("Alice"), lobby.join("Bob")
    lobby.start_formation()
    lobby.assign(1, alice)
    assert lobby.join_team(bob, lobby.team_code(1)).status_code == 200
    response = lobby.assign(2, bob)
    assert response.status_code == 409 and code_of(response) == "PLAYER_ON_OTHER_TEAM"


def test_member_can_be_promoted_to_leader_of_their_own_team(make_lobby):
    lobby = make_lobby()
    alice, bob = lobby.join("Alice"), lobby.join("Bob")
    lobby.start_formation()
    lobby.assign(1, alice)
    lobby.join_team(bob, lobby.team_code(1))
    lobby.host.delete(lobby.url(f"/teams/{lobby.team(1)['team_id']}/leader"))
    assert lobby.assign(1, bob).status_code == 200
    assert lobby.team(1)["leader_name"] == "Bob"


def test_removing_a_leader_revokes_the_team_code(make_lobby):
    lobby = make_lobby()
    alice, bob = lobby.join("Alice"), lobby.join("Bob")
    lobby.start_formation()
    lobby.assign(1, alice)
    old_code = lobby.team_code(1)

    response = lobby.host.delete(lobby.url(f"/teams/{lobby.team(1)['team_id']}/leader"))
    assert response.status_code == 200
    team = lobby.team(1)
    assert team["leader_name"] is None and team["team_code"] is None
    assert team["status"] == "WAITING_FOR_LEADER"

    stale = lobby.join_team(bob, old_code)
    assert stale.status_code == 404 and code_of(stale) == "INVALID_TEAM_CODE"
