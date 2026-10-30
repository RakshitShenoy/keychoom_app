"""Phase 4: Floor 1 (Candle -> Painting -> Vent -> Vault) from the first click to the key fragment."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from starlette.testclient import TestClient

from app.db.models import Game, ObjectState, Team, TeamFloorState
from play_support import CODE, SOLUTIONS, error_code, make_settings, reload, start_running, team_players


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


def team_id(lobby, slot):
    return lobby.team(slot)["team_id"]


# ---------------------------------------------------------------- starting / entering Floor 1
def test_start_puts_every_team_on_floor_1(make_lobby, db):
    lobby = start_running(make_lobby)
    assert lobby.lobby()["game_status"] == "RUNNING"
    for slot in (1, 2, 3):
        a, *_ = team_players(lobby, slot)
        state = a.state()
        assert state["game_status"] == "RUNNING" and state["team"]["status"] == "PLAYING"
        assert state["room"]["floor"] == 1 and state["room"]["floor_status"] == "ACTIVE"
        statuses = {o["id"]: o["status"] for o in state["room"]["objects"]}
        assert statuses == {"candle": "AVAILABLE", "painting": "LOCKED", "vent": "LOCKED"}
        assert state["room"]["vault"]["status"] == "LOCKED"
        assert state["room"]["vault"]["revealed"] == [None, None, None, None]
        assert state["room"]["fragment"]["awarded"] is False
        team = reload(db, Team, team_id(lobby, slot))
        assert team.status == "PLAYING" and team.current_floor == 1 and team.deadline_at is not None
        floor = db.query(TeamFloorState).filter_by(team_id=team.id, floor_no=1).one()
        assert floor.preset_id == "floor1_cellar" and floor.status == "ACTIVE"


def test_only_the_host_can_start_and_only_once(make_lobby):
    lobby = make_lobby(team_count=3, min_players=3, max_players=5)
    lobby.build_full_teams(3)
    player = team_players(lobby, 1)[0]
    assert player.client.post(lobby.url("/start")).status_code == 403  # a player is not the host
    assert lobby.host.post(lobby.url("/start")).status_code == 409  # formation not locked yet
    lobby.lock()
    assert lobby.host.post(lobby.url("/start")).status_code == 200
    assert lobby.host.post(lobby.url("/start")).status_code == 409  # already running


def test_gameplay_is_refused_before_the_game_starts(make_lobby):
    lobby = make_lobby(team_count=3, min_players=3, max_players=5)
    lobby.build_full_teams(3)
    lobby.lock()
    a = team_players(lobby, 1)[0]
    assert error_code(a.investigate("candle")) == "GAME_NOT_RUNNING"


# ---------------------------------------------------------------- interaction + the one-player lock
def test_valid_interaction_shows_the_challenge_to_the_investigator_only(make_lobby):
    lobby = start_running(make_lobby)
    a, b, _ = team_players(lobby, 1)
    response = a.investigate("candle")
    assert response.status_code == 200
    mine = response.json()["state"]
    assert a.obj("candle", mine)["held_by_me"] is True and a.obj("candle", mine)["status"] == "BEING_INVESTIGATED"
    challenge = mine["my_investigation"]["challenge"]
    assert challenge["title"] == "The Two Candles" and "answer" not in challenge

    seen = b.state()  # a teammate sees WHO is busy, but never the puzzle
    assert b.obj("candle", seen)["holder"] == a.player.name and b.obj("candle", seen)["held_by_me"] is False
    assert seen["my_investigation"] is None
    assert "Two Candles" not in json.dumps(seen)


def test_invalid_and_locked_objects_are_rejected(make_lobby):
    lobby = start_running(make_lobby)
    a, *_ = team_players(lobby, 1)
    assert error_code(a.investigate("painting")) == "OBJECT_LOCKED"  # needs the candle first
    assert error_code(a.investigate("vent")) == "OBJECT_LOCKED"
    assert a.investigate("nope").status_code == 404
    assert a.investigate("vault").status_code == 404  # the vault has its own endpoint
    assert a.investigate("bad id!").status_code == 422


def test_only_one_teammate_can_investigate_an_object(make_lobby):
    lobby = start_running(make_lobby)
    a, b, _ = team_players(lobby, 1)
    assert a.investigate("candle").status_code == 200
    busy = b.investigate("candle")
    assert busy.status_code == 409 and error_code(busy) == "OBJECT_BUSY"
    assert a.player.name in busy.json()["error"]["message"]
    assert a.investigate("candle").status_code == 200  # the holder may re-open it (idempotent)
    assert b.release("candle").status_code == 409  # cannot release someone else's lock
    assert a.release("candle").status_code == 200
    assert b.investigate("candle").status_code == 200  # free again


def test_solved_objects_stay_solved_and_free_the_next_one(make_lobby):
    lobby = start_running(make_lobby)
    a, *_ = team_players(lobby, 1)
    a.solve("candle")
    assert a.investigate("painting").status_code == 200
    assert error_code(a.investigate("candle")) == "OBJECT_SOLVED"
    assert a.release("painting").status_code == 200


def test_racing_teammates_exactly_one_wins(make_lobby):
    lobby = start_running(make_lobby)
    racers = team_players(lobby, 1)
    barrier = threading.Barrier(len(racers))

    def go(play):
        barrier.wait()
        return play.investigate("candle").status_code

    with ThreadPoolExecutor(max_workers=len(racers)) as pool:
        results = list(pool.map(go, racers))
    assert sorted(results) == [200, 409, 409]


def test_stale_lock_is_freed_by_the_sweeper(make_lobby, app):
    from datetime import timedelta
    from app.game import service
    from app.timers.clock import utcnow

    lobby = start_running(make_lobby)
    a, b, _ = team_players(lobby, 1)
    a.investigate("candle")
    assert b.investigate("candle").status_code == 409
    out = service.sweep(app.state.db.session_factory, app.state.game, utcnow() + timedelta(seconds=300))
    assert out["released"] == 1
    assert b.investigate("candle").status_code == 200  # the abandoned lease expired
    assert a.heartbeat("candle").status_code == 409  # and a's heartbeat no longer works


def test_heartbeat_keeps_the_lease_alive(make_lobby):
    lobby = start_running(make_lobby)
    a, b, _ = team_players(lobby, 1)
    a.investigate("candle")
    assert a.heartbeat("candle").status_code == 200
    assert b.heartbeat("candle").status_code == 409


# ---------------------------------------------------------------- challenges + progression
def test_answering_needs_the_lock(make_lobby):
    lobby = start_running(make_lobby)
    a, b, _ = team_players(lobby, 1)
    assert error_code(a.answer("candle", "3")) == "NOT_INVESTIGATING"  # never investigated
    a.investigate("candle")
    assert error_code(b.answer("candle", "3")) == "NOT_INVESTIGATING"  # teammate without the lock
    assert a.obj("candle")["status"] == "BEING_INVESTIGATED"  # nothing was solved


def test_incorrect_answer_changes_nothing_important(make_lobby, db):
    lobby = start_running(make_lobby)
    a, *_ = team_players(lobby, 1)
    a.investigate("candle")
    response = a.answer("candle", "5")
    assert response.status_code == 200 and response.json()["result"]["result"] == "WRONG"
    state = response.json()["state"]
    assert a.obj("candle", state)["status"] == "BEING_INVESTIGATED"
    assert state["room"]["vault"]["revealed"] == [None, None, None, None] and state["room"]["clues"] == []
    assert a.answer("candle", "abc").json()["result"]["result"] == "INVALID"  # not a number
    assert a.answer("candle", "3").json()["result"]["result"] == "CORRECT"  # still solvable


def test_correct_answer_solves_unlocks_next_and_reveals_a_vault_character(make_lobby, db):
    lobby = start_running(make_lobby)
    a, b, _ = team_players(lobby, 1)
    state = a.solve("candle").json()["state"]
    assert a.obj("candle", state)["status"] == "SOLVED" and a.obj("painting", state)["status"] == "AVAILABLE"
    assert state["room"]["vault"]["revealed"] == ["K", None, None, None]
    assert "THIRD frame" in state["room"]["clues"][0]["text"]
    assert state["my_investigation"] is None  # the lease ended with the solve
    assert error_code(a.investigate("candle")) == "OBJECT_SOLVED"
    assert error_code(a.answer("candle", "3")) in {"OBJECT_SOLVED", "NOT_INVESTIGATING"}
    row = db.query(ObjectState).filter_by(team_id=team_id(lobby, 1), object_id="candle").one()
    assert row.status == "SOLVED" and row.solved_by_player_id == a.player.id
    assert b.obj("painting")["status"] == "AVAILABLE"  # the teammate sees the progress too


def test_floor_one_end_to_end_with_vault_and_key_fragment(make_lobby, db):
    lobby = start_running(make_lobby)
    a, b, _ = team_players(lobby, 1)
    a.solve("candle")
    assert error_code(a.vault(CODE)) == "VAULT_LOCKED"  # not all objects solved yet
    b.solve("painting")
    state = a.state()
    assert state["room"]["vault"]["revealed"] == ["K", "7", "R", None]
    a.solve("vent")
    state = a.state()
    assert state["room"]["vault"]["status"] == "AVAILABLE" and state["room"]["vault"]["revealed"] == ["K", "7", "R", "4"]

    wrong = a.vault("K7R5")
    assert wrong.json()["result"]["result"] == "WRONG"
    assert wrong.json()["state"]["room"]["fragment"]["awarded"] is False
    assert wrong.json()["state"]["room"]["vault"]["attempts"] == 1
    assert a.vault("K7").json()["result"]["result"] == "INVALID"  # wrong length: not counted as a try

    done = b.vault("k7-r4")  # normalised: case and dashes do not matter
    assert done.json()["result"]["result"] == "CORRECT"
    room = done.json()["state"]["room"]
    assert room["floor_status"] == "COMPLETED" and room["vault"]["status"] == "OPEN"
    assert room["fragment"]["awarded"] is True

    team = reload(db, Team, team_id(lobby, 1))
    floor = db.query(TeamFloorState).filter_by(team_id=team.id).one()
    assert floor.fragment_awarded is True and floor.status == "COMPLETED" and floor.completed_at is not None
    assert team.floors_completed == 1 and team.status == "PLAYING"
    assert error_code(a.vault(CODE)) == "FLOOR_COMPLETED"  # no double award
    assert error_code(a.investigate("candle")) == "FLOOR_COMPLETED"


def test_two_teammates_opening_the_vault_at_once_award_one_fragment(make_lobby, db):
    lobby = start_running(make_lobby)
    players = team_players(lobby, 1)
    for oid in ("candle", "painting", "vent"):
        players[0].solve(oid)
    barrier = threading.Barrier(3)

    def go(play):
        barrier.wait()
        return play.vault(CODE).status_code

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(go, players))
    assert sorted(results) == [200, 409, 409]
    assert db.query(TeamFloorState).filter_by(team_id=team_id(lobby, 1), fragment_awarded=True).count() == 1


def test_wrong_answer_cooldown(make_lobby, app):
    lobby = start_running(make_lobby)
    app.state.game.settings.answer_cooldown_seconds = 30
    a, *_ = team_players(lobby, 1)
    a.investigate("candle")
    assert a.answer("candle", "9").json()["result"]["result"] == "WRONG"
    blocked = a.answer("candle", "3")
    assert blocked.status_code == 429 and error_code(blocked) == "ANSWER_COOLDOWN"


# ---------------------------------------------------------------- persistence / reconnect
def test_progress_survives_refresh_and_a_new_session_view(make_lobby, db):
    lobby = start_running(make_lobby)
    a, b, _ = team_players(lobby, 1)
    a.solve("candle")
    a.investigate("painting")
    for fresh in (a.state(), b.state()):  # a page refresh = a fresh GET of the server state
        assert a.obj("candle", fresh)["status"] == "SOLVED"
        assert fresh["room"]["vault"]["revealed"][0] == "K" and fresh["room"]["clues"]
    assert a.state()["my_investigation"]["object_id"] == "painting"  # even my open investigation
    rows = db.query(ObjectState).filter_by(team_id=team_id(lobby, 1), status="SOLVED").all()
    assert [r.object_id for r in rows] == ["candle"]  # it is in the database, not in browser memory
    floor = db.query(TeamFloorState).filter_by(team_id=team_id(lobby, 1)).one()
    assert floor.vault_pieces == {"0": "K"}


def test_websocket_connect_and_reconnect_receive_the_floor_state(make_lobby, live):
    lobby = start_running(make_lobby)
    a, b, _ = team_players(lobby, 1)
    a.solve("candle")
    first = lobby.player_socket(live, a.player)
    message = first.wait_for_type("FLOOR_STATE")
    assert a.obj("candle", message["payload"])["status"] == "SOLVED"
    first.close()  # the tab dies ...
    second = lobby.player_socket(live, a.player)  # ... and comes back
    again = second.wait_for_type("FLOOR_STATE")
    assert again["payload"]["room"]["vault"]["revealed"][0] == "K"


def test_team_changes_are_pushed_to_teammates_in_real_time(make_lobby, live):
    lobby = start_running(make_lobby)
    a, b, _ = team_players(lobby, 1)
    sock_b = lobby.player_socket(live, b.player)
    sock_b.wait_for_type("FLOOR_STATE")

    a.investigate("candle")
    locked = sock_b.wait_for_type("OBJECT_LOCKED")
    assert locked["payload"]["label"] == "Black Candle" and "player_id" not in locked["payload"]
    busy = sock_b.wait_for(
        lambda m: m["type"] == "FLOOR_STATE" and b.obj("candle", m["payload"])["status"] == "BEING_INVESTIGATED")
    assert busy["payload"]["my_investigation"] is None  # no puzzle content for the teammate

    a.answer("candle", "3")
    assert sock_b.wait_for_type("OBJECT_SOLVED")["payload"]["label"] == "Black Candle"
    assert sock_b.wait_for_type("VAULT_UPDATED")
    solved = sock_b.wait_for(lambda m: m["type"] == "FLOOR_STATE" and b.obj("candle", m["payload"])["status"] == "SOLVED")
    assert solved["payload"]["room"]["vault"]["revealed"][0] == "K"

    a.solve("painting"); a.solve("vent")
    a.vault(CODE)
    for kind in ("VAULT_OPENED", "FRAGMENT_AWARDED", "FLOOR_COMPLETED"):
        sock_b.wait_for_type(kind)


# ---------------------------------------------------------------- isolation + tampering
def test_teams_are_independent_and_do_not_see_each_others_events(make_lobby, live):
    lobby = start_running(make_lobby)
    a1, *_ = team_players(lobby, 1)
    a2, b2, _ = team_players(lobby, 2)
    other = lobby.player_socket(live, b2.player)
    other.wait_for_type("FLOOR_STATE")
    other.collect(0.3)

    a1.solve("candle")
    assert a2.investigate("candle").status_code == 200  # team 1's progress/lock never blocks team 2
    assert a2.obj("candle")["status"] == "BEING_INVESTIGATED"
    assert b2.state()["room"]["vault"]["revealed"] == [None, None, None, None]
    types = [m["type"] for m in other.collect(0.8) if isinstance(m, dict)]
    assert "OBJECT_SOLVED" not in types and "VAULT_UPDATED" not in types
    assert "OBJECT_LOCKED" in types  # only its own team's lock event


def test_games_are_isolated(make_lobby, second_host, harness):
    lobby = start_running(make_lobby)
    response = second_host.post("/api/games", json={"team_count": 3, "min_players_per_team": 3, "max_players_per_team": 5})
    other = harness(second_host, response.json())
    other.build_full_teams(3)
    other.lock()
    assert other.host.post(other.url("/start")).status_code == 200
    mine = team_players(lobby, 1)[0]
    theirs = team_players(other, 1)[0]
    mine.solve("candle")
    assert theirs.obj("candle")["status"] == "AVAILABLE"  # same room, different game
    wrong_game = mine.client.post(other.url("/play/objects/candle/investigate"))
    assert wrong_game.status_code == 403 and error_code(wrong_game) == "WRONG_GAME"


def test_browsers_cannot_manipulate_state(make_lobby, live):
    lobby = start_running(make_lobby)
    a, *_ = team_players(lobby, 1)
    a.investigate("candle")
    for body in ({"answer": "3", "solved": True}, {"answer": "3", "team_id": "x"}, {"answer": "3", "reward": "K"}):
        assert a.call("POST", "/objects/candle/answer", body).status_code == 422  # unknown fields refused
    assert a.call("POST", "/hints", {"level": 1, "penalty_seconds": 0}).status_code == 422
    assert a.call("POST", "/vault", {"code": CODE, "open": True}).status_code == 422
    assert a.obj("candle")["status"] == "BEING_INVESTIGATED"

    stranger = TestClient(lobby.app)  # no session at all
    assert stranger.get(lobby.url("/play/state")).status_code == 401
    assert stranger.post(lobby.url("/play/objects/candle/investigate")).status_code == 401
    assert lobby.host.get(lobby.url("/play/state")).status_code == 401  # a host is not a player

    sock = lobby.player_socket(live, a.player)
    sock.wait_for_type("FLOOR_STATE")
    sock.send({"type": "OBJECT_SOLVED", "payload": {"object_id": "candle"}})  # the socket never accepts state
    assert sock.wait_for_type("ERROR")["code"] == "UNSUPPORTED_MESSAGE"
    assert a.obj("candle")["status"] == "BEING_INVESTIGATED"
