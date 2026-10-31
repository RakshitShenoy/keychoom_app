from fastapi.testclient import TestClient


def code_of(response):
    return response.json()["error"]["code"]


def test_host_can_start_team_formation(make_lobby):
    lobby = make_lobby()
    lobby.join_many(3)
    view = lobby.start_formation()
    assert view["lobby_state"] == "TEAM_FORMATION" and view["game_status"] == "TEAM_FORMATION"
    assert view["host_actions"]["can_start_formation"] is False
    assert view["chat"]["enabled"] is False


def test_players_can_still_join_the_lobby_during_formation(make_lobby):
    lobby = make_lobby()
    lobby.start_formation()
    assert lobby.join("Late Larry").name == "Late Larry"
    assert lobby.lobby()["counts"]["player_count"] == 1


def test_only_the_host_can_control_formation(make_lobby):
    lobby = make_lobby()
    alice = lobby.join("Alice")
    for action in ("start", "lock", "reopen"):
        response = alice.client.post(lobby.url(f"/team-formation/{action}"))
        assert response.status_code == 403 and code_of(response) == "HOST_ONLY"
        anonymous = TestClient(lobby.app).post(lobby.url(f"/team-formation/{action}"))
        assert anonymous.status_code == 401
    assert lobby.lobby()["lobby_state"] == "WAITING"


def test_formation_cannot_be_locked_while_requirements_are_unmet(make_lobby):
    lobby = make_lobby()
    players = lobby.join_many(4)
    lobby.start_formation()
    lobby.assign(1, players[0])
    response = lobby.lock()
    assert response.status_code == 409
    assert code_of(response) == "REQUIREMENTS_NOT_MET"
    problems = {d["code"] for d in response.json()["error"]["details"]}
    assert {"TEAM_NO_LEADER", "TEAM_TOO_SMALL", "UNASSIGNED_PLAYERS"} <= problems
    assert lobby.lobby()["lobby_state"] == "TEAM_FORMATION"


def test_lock_requires_open_formation(make_lobby):
    lobby = make_lobby()
    response = lobby.lock()  # still WAITING
    assert response.status_code == 409 and code_of(response) == "INVALID_LOBBY_STATE"


def test_host_can_lock_and_membership_becomes_fixed(make_lobby):
    lobby = make_lobby()
    lobby.build_full_teams()
    response = lobby.lock()
    assert response.status_code == 200
    view = response.json()
    assert view["team_formation_locked"] and view["lobby_state"] == "READY_TO_START"
    assert view["host_actions"]["can_lock"] is False and view["host_actions"]["can_reopen"]

    before = [(t["slot_no"], sorted(t["members"])) for t in view["teams"]]
    # nobody can change anything while locked
    assert lobby.join_team(lobby.players[1], lobby.team_code(2)).status_code == 409
    assert lobby.assign(1, lobby.players[4]).status_code == 409
    assert lobby.kick(lobby.players[2]).status_code == 409
    assert lobby.players[2].client.post(lobby.url("/leave")).status_code == 409
    assert lobby.join_team(lobby.players[1], lobby.team_code(1)).status_code == 409  # already on a team
    after = [(t["slot_no"], sorted(t["members"])) for t in lobby.lobby()["teams"]]
    assert before == after


def test_new_players_cannot_join_a_locked_game(make_lobby):
    lobby = make_lobby()
    lobby.build_full_teams()
    lobby.lock()
    late = TestClient(lobby.app).post(
        "/api/games/join", json={"game_code": lobby.code, "display_name": "Too Late"}
    )
    assert late.status_code == 409


def test_reopen_allows_changes_again(make_lobby):
    lobby = make_lobby()
    lobby.build_full_teams()
    lobby.lock()
    response = lobby.host.post(lobby.url("/team-formation/reopen"))
    assert response.status_code == 200
    view = response.json()
    assert view["lobby_state"] == "TEAM_FORMATION" and not view["team_formation_locked"]
    assert lobby.kick(lobby.players[2]).status_code == 200
    assert lobby.lock().status_code == 409  # a team is now too small
    assert lobby.host.post(lobby.url("/team-formation/reopen")).status_code == 409  # not locked


# ---- kick / leave ---------------------------------------------------------------------------
def test_kicked_player_loses_access(make_lobby):
    lobby = make_lobby()
    alice, bob = lobby.join("Alice"), lobby.join("Bob")
    assert alice.client.get(lobby.url("/lobby")).status_code == 200

    response = lobby.kick(alice)
    assert response.status_code == 200
    assert [p["display_name"] for p in response.json()["players"]] == ["Bob"]

    assert alice.client.get(lobby.url("/lobby")).status_code == 401
    assert alice.client.get("/api/me").json()["player"] is None
    assert alice.client.post(lobby.url("/chat"), json={"body": "hi"}).status_code == 401
    assert alice.client.post(lobby.url("/teams/join"), json={"team_code": "TEAM-AAAA"}).status_code == 401
    assert bob.client.get(lobby.url("/lobby")).status_code == 200


def test_kicking_removes_team_membership_and_updates_readiness(make_lobby):
    lobby = make_lobby()
    lobby.build_full_teams()  # 3 teams of exactly 3 (the minimum)
    member = lobby.players[1]  # member of team 1
    assert lobby.team(1)["status"] == "READY"
    assert lobby.kick(member).status_code == 200
    team = lobby.team(1)
    assert team["player_count"] == 2 and team["status"] == "WAITING_FOR_PLAYERS"
    assert member.name not in team["members"]
    assert lobby.lobby()["readiness"]["requirements_met"] is False


def test_kicking_a_leader_revokes_the_team_code(make_lobby):
    lobby = make_lobby()
    lobby.build_full_teams()
    leader, member = lobby.players[0], lobby.players[1]
    old_code = lobby.team_code(1)
    assert lobby.kick(leader).status_code == 200
    team = lobby.team(1)
    assert team["leader_name"] is None and team["team_code"] is None
    assert team["status"] == "WAITING_FOR_LEADER" and team["player_count"] == 2
    # the old code is dead for newcomers too
    newcomer = lobby.join("Newcomer")
    assert lobby.join_team(newcomer, old_code).status_code == 404


def test_player_can_leave_the_lobby(make_lobby):
    lobby = make_lobby()
    alice, bob = lobby.join("Alice"), lobby.join("Bob")
    assert alice.client.post(lobby.url("/leave")).json() == {"left": True}
    assert [p["display_name"] for p in lobby.lobby()["players"]] == ["Bob"]
    assert alice.client.get("/api/me").json()["player"] is None


def test_only_the_host_can_kick(make_lobby):
    lobby = make_lobby()
    alice, bob = lobby.join("Alice"), lobby.join("Bob")
    response = alice.client.post(lobby.url(f"/players/{bob.id}/kick"))
    assert response.status_code == 403 and code_of(response) == "HOST_ONLY"
    assert lobby.lobby()["counts"]["player_count"] == 2


def test_kicked_name_can_be_used_again(make_lobby):
    lobby = make_lobby()
    alice = lobby.join("Alice")
    lobby.kick(alice)
    assert lobby.join("Alice").name == "Alice"


def test_every_change_is_written_to_the_audit_log(make_lobby, db):
    from sqlalchemy import select

    from app.db.models import GameEvent

    lobby = make_lobby()
    alice, bob = lobby.join("Alice"), lobby.join("Bob")
    lobby.start_formation()
    lobby.assign(1, alice)
    lobby.join_team(bob, lobby.team_code(1))
    lobby.kick(bob)
    kinds = [
        e.event_type
        for e in db.scalars(
            select(GameEvent).where(GameEvent.game_id == lobby.game["game_id"]).order_by(GameEvent.created_at)
        ).all()
    ]
    for expected in (
        "GAME_CREATED", "PLAYER_JOINED", "TEAM_FORMATION_STARTED",
        "LEADER_ASSIGNED", "TEAM_JOINED", "PLAYER_KICKED",
    ):
        assert expected in kinds


def test_events_are_published_on_the_bus(make_lobby, app):
    from app.lobby.events import LobbyEventType

    seen = []
    app.state.lobby_bus.subscribe(lambda event: seen.append(event.type))
    lobby = make_lobby()
    alice = lobby.join("Alice")
    lobby.start_formation()
    lobby.assign(1, alice)
    assert LobbyEventType.PLAYER_JOINED in seen
    assert LobbyEventType.TEAM_FORMATION_STARTED in seen
    assert LobbyEventType.LEADER_ASSIGNED in seen
    assert LobbyEventType.LOBBY_UPDATED in seen
