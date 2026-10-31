"""Phase 12: the smallest valid game is 3 teams x 1 player. Every team needs a leader and >= 1 player.

These tests deliberately use the DEFAULT configuration (no ``min_players_per_team`` in the request), so
they exercise the real rule and not a test-side override.
"""
import pytest
from fastapi.testclient import TestClient

from app.db.models import Game, Team, TeamFloorState
from play_support import (
    error_code, final_code, make_settings, play_whole_building, reload, team_players,
    load_library,
)


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def lobby_for(client, harness):
    """lobby_for(team_count, **config) -> harness, created with the real defaults."""

    def _make(team_count=3, **config):
        response = client.post("/api/games", json={"team_count": team_count, **config})
        assert response.status_code == 201, response.text
        return harness(client, response.json())

    return _make


def fill(lobby, sizes):
    """Fill team slot i with sizes[i-1] players; the first of each team becomes its leader."""
    lobby.join_many(sum(sizes))
    lobby.start_formation()
    cursor = 0
    for slot, size in enumerate(sizes, start=1):
        block = lobby.players[cursor : cursor + size]
        cursor += size
        if not block:
            continue
        assert lobby.assign(slot, block[0]).status_code == 200
        code = lobby.team_code(slot)
        for member in block[1:]:
            assert lobby.join_team(member, code).status_code == 200


def start(lobby):
    return lobby.host.post(lobby.url("/start"))


# ------------------------------------------------------------------ the rule itself
def test_defaults_are_three_teams_and_one_player_per_team(settings, lobby_for):
    assert (settings.min_teams_limit, settings.max_teams_limit) == (3, 25)
    assert (settings.min_players_limit, settings.max_players_limit) == (1, 10)
    lobby = lobby_for(3)
    assert lobby.game["config"]["min_players_per_team"] == 1
    counts = lobby.lobby()["counts"]
    assert counts["min_total_players"] == 3  # derived: teams x minimum, never typed in by the host


def test_3_teams_x_1_player_can_start(lobby_for):  # test 1
    lobby = lobby_for(3)
    fill(lobby, [1, 1, 1])
    view = lobby.lobby()
    assert view["readiness"]["requirements_met"] and view["counts"]["player_count"] == 3
    assert all(t["status"] == "READY" for t in view["teams"])  # a lone leader is a valid team
    assert lobby.lock().status_code == 200
    assert lobby.lobby()["host_actions"]["can_start_game"] is True
    assert start(lobby).status_code == 200
    assert lobby.lobby()["game_status"] == "RUNNING"


def test_an_empty_team_blocks_the_game_with_a_clear_reason(lobby_for):  # test 2
    lobby = lobby_for(3)
    fill(lobby, [1, 1, 0])
    view = lobby.lobby()
    assert view["readiness"]["requirements_met"] is False
    assert view["host_actions"]["can_lock"] is False and view["host_actions"]["can_start_game"] is False
    messages = " ".join(p["message"] for p in view["readiness"]["problems"])
    assert "Every team must have at least 1 player." in messages
    locked = lobby.lock()
    assert locked.status_code == 409 and error_code(locked) == "REQUIREMENTS_NOT_MET"
    assert "TEAM_TOO_SMALL" in {d["code"] for d in locked.json()["error"]["details"]}
    assert start(lobby).status_code == 409  # not locked, so the game cannot start either
    assert lobby.lobby()["game_status"] == "TEAM_FORMATION"


def test_empty_team_cannot_be_started_even_if_the_database_says_ready(lobby_for, db):
    """start_game re-checks readiness itself: a forced READY status is not enough."""
    lobby = lobby_for(3)
    fill(lobby, [1, 1, 0])
    game = db.get(Game, lobby.game["game_id"])
    game.status = "READY"
    db.commit()
    refused = start(lobby)
    assert refused.status_code == 409 and error_code(refused) == "REQUIREMENTS_NOT_MET"


def test_uneven_teams_1_2_1_can_start(lobby_for):  # test 3
    lobby = lobby_for(3)
    fill(lobby, [1, 2, 1])
    assert lobby.lock().status_code == 200
    assert start(lobby).status_code == 200


def test_25_teams_x_1_player_is_a_valid_game(lobby_for):  # test 4
    lobby = lobby_for(25)
    assert lobby.lobby()["counts"]["min_total_players"] == 25
    fill(lobby, [1] * 25)
    assert lobby.lobby()["readiness"]["requirements_met"]
    assert lobby.lock().status_code == 200
    assert start(lobby).status_code == 200


def test_capacity_above_ten_is_rejected(client):  # test 5
    response = client.post("/api/games", json={"team_count": 3, "max_players_per_team": 11})
    assert response.status_code == 422
    assert "max_players_per_team" in [d["field"] for d in response.json()["error"]["details"]]
    assert client.post("/api/games", json={"team_count": 3, "max_players_per_team": 10}).status_code == 201


@pytest.mark.parametrize("count", [0, 1, 2, 26])
def test_fewer_than_three_or_more_than_25_teams_rejected(client, count):  # test 6
    response = client.post("/api/games", json={"team_count": count})
    assert response.status_code == 422
    assert "team_count" in [d["field"] for d in response.json()["error"]["details"]]


def test_zero_minimum_per_team_is_still_rejected(client):
    assert client.post("/api/games", json={"team_count": 3, "min_players_per_team": 0}).status_code == 422


def test_all_players_in_one_team_cannot_start(lobby_for):  # test 7
    lobby = lobby_for(3, max_players_per_team=10)
    fill(lobby, [6, 0, 0])
    view = lobby.lobby()
    assert view["counts"]["unassigned_count"] == 0 and view["readiness"]["requirements_met"] is False
    assert [t["status"] for t in view["teams"]][1:] == ["WAITING_FOR_LEADER"] * 2
    assert lobby.lock().status_code == 409 and start(lobby).status_code == 409


def test_a_leaderless_team_with_players_is_still_blocked(lobby_for):
    lobby = lobby_for(3)
    players = lobby.join_many(4)
    lobby.start_formation()
    lobby.assign(1, players[0])
    lobby.assign(2, players[1])  # team 3 has no leader and nobody on it; player 4 unassigned
    codes = {p["code"] for p in lobby.lobby()["readiness"]["problems"]}
    assert {"TEAM_NO_LEADER", "TEAM_TOO_SMALL", "UNASSIGNED_PLAYERS"} <= codes


def test_the_host_page_states_the_rule_and_has_no_old_requirement(client):
    html = client.get("/host").text
    assert "Every team must have at least 1 player before the game can start." in html
    assert 'id="min_players_per_team"' not in html  # the minimum is derived, never typed in
    assert "9 players" not in html and "nine" not in html.lower()


# ------------------------------------------------------------------ single-player teams and sessions
def test_a_disconnected_single_player_team_stays_a_valid_team(lobby_for, live):  # test 8
    lobby = lobby_for(3)
    fill(lobby, [1, 1, 1])
    host = lobby.host_socket(live)
    host.wait_for_type("LOBBY_STATE")
    sock = lobby.player_socket(live, lobby.players[0])
    sock.wait_for_type("LOBBY_STATE")
    sock.close()
    state = host.wait_for_state(lambda s: not next(
        p for p in s["players"] if p["display_name"] == lobby.players[0].name)["is_connected"])
    assert state["counts"]["player_count"] == 3  # presence is not membership
    assert state["readiness"]["requirements_met"] is True
    assert next(t for t in state["teams"] if t["slot_no"] == 1)["status"] == "READY"
    assert lobby.lock().status_code == 200 and start(lobby).status_code == 200


def test_refresh_keeps_the_same_session_and_display_name(lobby_for):  # test 9
    lobby = lobby_for(3)
    fill(lobby, [1, 1, 1])
    player = lobby.players[1]
    before = player.client.get("/api/me").json()["player"]
    for _ in range(3):  # a refresh is a new request carrying the same cookie
        again = player.client.get("/api/me").json()["player"]
        assert again["player_id"] == before["player_id"] == player.id
        assert again["display_name"] == player.name and again["team_id"] == before["team_id"]
    assert player.client.get(lobby.url("/lobby")).json()["you"]["display_name"] == player.name


def test_reconnect_restores_player_team_and_state(lobby_for, live):  # test 10
    lobby = lobby_for(3)
    fill(lobby, [1, 1, 1])
    member = lobby.players[2]
    first = lobby.player_socket(live, member)
    before = first.wait_for_type("LOBBY_STATE")["payload"]
    first.close()
    lobby.lock()
    second = lobby.player_socket(live, member)
    after = second.wait_for_type("LOBBY_STATE")["payload"]
    assert after["you"]["player_id"] == before["you"]["player_id"] == member.id
    assert after["you"]["team_id"] == before["you"]["team_id"] and after["you"]["display_name"] == member.name
    assert after["lobby_state"] == "READY_TO_START"
    assert lobby.lobby()["counts"]["player_count"] == 3  # no duplicate player rows


def test_host_refresh_restores_the_dashboard(lobby_for, copy_cookies):  # test 11
    lobby = lobby_for(3)
    fill(lobby, [1, 1, 1])
    lobby.lock()
    assert start(lobby).status_code == 200
    refreshed = TestClient(lobby.app)  # the host reloads the page: a fresh browser with the same cookie
    copy_cookies(lobby.host, refreshed)
    assert refreshed.get("/api/me").json()["host"]["game_code"] == lobby.code
    view = refreshed.get(lobby.url("/lobby")).json()
    assert view["you"]["role"] == "host" and view["game_status"] == "RUNNING"
    dashboard = refreshed.get(lobby.url("/play/host"))
    assert dashboard.status_code == 200, dashboard.text
    assert len(dashboard.json()["teams"]) == 3


def test_display_name_rules_still_hold(lobby_for):
    lobby = lobby_for(3)
    lobby.join("Alice")
    other = TestClient(lobby.app).post(
        "/api/games/join", json={"game_code": lobby.code, "display_name": "  ALICE "}
    )
    assert other.status_code == 409 and error_code(other) == "DISPLAY_NAME_TAKEN"
    trimmed = TestClient(lobby.app).post(
        "/api/games/join", json={"game_code": lobby.code, "display_name": "   Bob   Smith  "}
    )
    assert trimmed.status_code == 201 and trimmed.json()["display_name"] == "Bob Smith"


# ------------------------------------------------------------------ a whole game with one player per team
def test_three_solo_teams_play_a_complete_game_and_one_wins(lobby_for, db):  # test 12
    lobby = lobby_for(3)
    fill(lobby, [1, 1, 1])
    assert lobby.lock().status_code == 200 and start(lobby).status_code == 200
    solo = [team_players(lobby, slot, per_team=1) for slot in (1, 2, 3)]
    assert all(len(team) == 1 for team in solo)
    state = solo[0][0].state()
    assert state["team"]["status"] == "PLAYING" and len(state["members"]) == 1

    played = play_whole_building(solo[0], load_library())
    assert sorted(played) == list(range(1, 9))
    final = solo[0][0].call("POST", "/final-door", {"code": final_code(played)})
    assert final.status_code == 200 and final.json()["result"]["result"] == "CORRECT"

    game = reload(db, Game, lobby.game["game_id"])
    winner = reload(db, Team, lobby.team(1)["team_id"])
    assert game.status == "FINISHED" and game.winner_team_id == winner.id and winner.status == "ESCAPED"
    assert db.query(TeamFloorState).filter_by(team_id=winner.id, fragment_awarded=True).count() == 8
    for slot in (2, 3):  # the others never touched the building
        assert db.query(TeamFloorState).filter_by(
            team_id=lobby.team(slot)["team_id"], fragment_awarded=True).count() == 0


def test_landing_offers_host_and_player_and_the_polish_stylesheet_is_served(client):
    html = client.get("/").text
    assert "Escape the Haunted House" in html
    assert "a class=\"btn\" href=\"/host\"" in html and "a class=\"btn\" href=\"/player\"" in html
    assert "/static/css/polish.css" in html
    assert client.get("/static/css/polish.css").status_code == 200
