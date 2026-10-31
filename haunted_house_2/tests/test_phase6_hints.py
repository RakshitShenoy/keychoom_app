"""Phase 6: team hints, penalties and the server-authoritative deadline."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import Game, HintUsage, Team
from app.game import service
from app.timers.clock import utcnow
from app.timers.deadline import compute_deadline, compute_team_deadline
from play_support import CODE, CONTENT_DIR, error_code, make_settings, reload, start_running, team_players

LADDER = json.loads((CONTENT_DIR / "presets" / "floor1_cellar.json").read_text())["hints"]
PENALTIES = {1: 30, 2: 60, 3: 90, 4: 120}


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


def team_id(lobby, slot):
    return lobby.team(slot)["team_id"]


def game_row(db, lobby):
    db.expire_all()
    return db.query(Game).filter_by(game_code=lobby.code).one()


# ---------------------------------------------------------------- deadline arithmetic
def test_deadline_formula_is_start_plus_base_minus_penalties():
    start = datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc)
    assert compute_team_deadline(start, 3600, 0) == start + timedelta(hours=1)
    assert compute_team_deadline(start, 3600, 90) == start + timedelta(seconds=3510)  # a penalty COSTS time
    assert compute_deadline(start, 3600, 90) == start + timedelta(seconds=3690)  # Phase 1 helper untouched
    with pytest.raises(ValueError):
        compute_team_deadline(datetime(2026, 1, 1), 3600)  # naive datetimes are refused


def test_each_team_gets_start_plus_base_as_its_initial_deadline(make_lobby, db):
    lobby = start_running(make_lobby, base_duration_seconds=1800)
    game = game_row(db, lobby)
    assert game.started_at is not None
    for slot in (1, 2, 3):
        team = reload(db, Team, team_id(lobby, slot))
        assert team.deadline_at == game.started_at + timedelta(seconds=1800) and team.hint_penalty_seconds == 0
    state = team_players(lobby, 1)[0].state()
    assert state["timer"]["seconds_remaining"] > 1790 and state["timer"]["expired"] is False
    assert state["timer"]["server_now"] and state["timer"]["deadline_at"]


# ---------------------------------------------------------------- consuming hints
def test_four_levels_are_offered_with_their_penalties(make_lobby):
    lobby = start_running(make_lobby)
    hints = team_players(lobby, 1)[0].state()["hints"]
    assert hints["max"] == 4 and hints["remaining"] == 4 and hints["used"] == 0 and hints["available"] is True
    assert [(h["level"], h["penalty_seconds"], h["used"]) for h in hints["levels"]] == [
        (1, 30, False), (2, 60, False), (3, 90, False), (4, 120, False)]
    assert hints["revealed"] == []


def test_using_a_hint_records_penalty_moves_the_deadline_and_reveals_text(make_lobby, db):
    lobby = start_running(make_lobby)
    a, b, _ = team_players(lobby, 1)
    before = reload(db, Team, team_id(lobby, 1)).deadline_at
    response = a.hint(1)
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["penalty_seconds"] == 30 and result["text"] == LADDER["candle"][0] and result["about"] == "Black Candle"

    team = reload(db, Team, team_id(lobby, 1))
    assert team.hint_penalty_seconds == 30 and team.deadline_at == before - timedelta(seconds=30)
    assert team.deadline_at == game_row(db, lobby).started_at + timedelta(seconds=3600 - 30)
    row = db.query(HintUsage).filter_by(team_id=team.id).one()
    assert (row.hint_number, row.penalty_seconds, row.object_id, row.requested_by_player_id) == (1, 30, "candle", a.player.id)

    hints = response.json()["state"]["hints"]
    assert hints["remaining"] == 3 and hints["used"] == 1 and hints["total_penalty_seconds"] == 30
    assert response.json()["state"]["timer"]["penalty_seconds"] == 30
    assert [h["used"] for h in hints["levels"]] == [True, False, False, False]
    assert b.state()["hints"]["revealed"][0]["text"] == LADDER["candle"][0]  # the whole team sees it


def test_hints_belong_to_the_team_not_the_player(make_lobby):
    lobby = start_running(make_lobby)
    a, b, _ = team_players(lobby, 1)
    assert a.hint(1).status_code == 200
    again = b.hint(1)  # a teammate cannot spend the same hint again
    assert again.status_code == 409 and error_code(again) == "HINT_ALREADY_USED"
    assert b.hint(2).status_code == 200
    assert a.state()["hints"]["remaining"] == 2


def test_hint_text_follows_the_teams_current_objective(make_lobby):
    lobby = start_running(make_lobby)
    a, *_ = team_players(lobby, 1)
    a.solve("candle")
    result = a.hint(2).json()["result"]
    assert result["about"] == "Family Portraits" and result["text"] == LADDER["painting"][1]


def test_penalties_follow_the_schedule_and_the_fifth_hint_is_rejected(make_lobby, db):
    lobby = start_running(make_lobby)
    a, *_ = team_players(lobby, 1)
    start = reload(db, Team, team_id(lobby, 1)).deadline_at
    spent = 0
    for level in (1, 2, 3, 4):
        response = a.hint(level)
        assert response.status_code == 200, response.text
        spent += PENALTIES[level]
        assert response.json()["result"]["penalty_seconds"] == PENALTIES[level]
        assert response.json()["state"]["hints"]["remaining"] == 4 - level
    team = reload(db, Team, team_id(lobby, 1))
    assert team.hint_penalty_seconds == spent == 300 and team.deadline_at == start - timedelta(seconds=300)
    fifth = a.hint(1)
    assert fifth.status_code == 409 and error_code(fifth) == "NO_HINTS_LEFT"
    assert db.query(HintUsage).filter_by(team_id=team.id).count() == 4
    assert reload(db, Team, team.id).hint_penalty_seconds == 300  # unchanged by the refused attempt
    assert a.state()["hints"]["available"] is False


def test_invalid_hint_levels_are_rejected(make_lobby):
    lobby = start_running(make_lobby)
    a, *_ = team_players(lobby, 1)
    assert a.hint(0).status_code == 422
    assert error_code(a.hint(5)) == "INVALID_HINT_LEVEL"
    assert a.call("POST", "/hints", {"level": "one"}).status_code == 422


def test_the_client_cannot_choose_or_reset_its_penalty(make_lobby, db):
    lobby = start_running(make_lobby)
    a, *_ = team_players(lobby, 1)
    for body in ({"level": 1, "penalty_seconds": 0}, {"level": 1, "hint_penalty_seconds": 0},
                 {"level": 1, "deadline_at": "2099-01-01T00:00:00+00:00"}, {"reset": True}):
        assert a.call("POST", "/hints", body).status_code == 422
    assert reload(db, Team, team_id(lobby, 1)).hint_penalty_seconds == 0
    a.hint(3)
    assert a.call("POST", "/hints", {"level": 3}).status_code == 409  # cannot be re-used to "reset"
    assert reload(db, Team, team_id(lobby, 1)).hint_penalty_seconds == 90


def test_a_hint_that_would_exhaust_the_clock_is_refused(make_lobby, db):
    lobby = start_running(make_lobby, base_duration_seconds=300)
    a, *_ = team_players(lobby, 1)
    game = game_row(db, lobby)
    game.started_at = utcnow() - timedelta(seconds=280)  # 20 seconds are left
    team = db.get(Team, team_id(lobby, 1))
    team.deadline_at = game.started_at + timedelta(seconds=300)
    db.commit()
    refused = a.hint(1)  # -30s would put the deadline in the past
    assert refused.status_code == 409 and error_code(refused) == "NOT_ENOUGH_TIME"
    assert reload(db, Team, team.id).hint_penalty_seconds == 0 and db.query(HintUsage).count() == 0


def test_no_hints_after_the_floor_is_complete(make_lobby):
    lobby = start_running(make_lobby)
    a, *_ = team_players(lobby, 1)
    for oid in ("candle", "painting", "vent"):
        a.solve(oid)
    assert a.vault(CODE).json()["result"]["result"] == "CORRECT"
    assert error_code(a.hint(1)) == "FLOOR_COMPLETED"


# ---------------------------------------------------------------- persistence, reconnect, sync, isolation
def test_hints_survive_refresh_and_reconnect(make_lobby, live, db):
    lobby = start_running(make_lobby)
    a, b, _ = team_players(lobby, 1)
    a.hint(2)
    assert b.state()["hints"]["used"] == 1 and a.state()["hints"]["revealed"][0]["level"] == 2  # refresh
    sock = lobby.player_socket(live, b.player)
    snapshot = sock.wait_for_type("FLOOR_STATE")["payload"]  # reconnect = a full snapshot
    assert snapshot["hints"]["used"] == 1 and snapshot["hints"]["total_penalty_seconds"] == 60
    assert snapshot["hints"]["revealed"][0]["text"] == LADDER["candle"][1]
    assert db.query(HintUsage).count() == 1  # persisted, not browser memory


def test_every_teammate_receives_the_hint_event_and_new_state(make_lobby, live):
    lobby = start_running(make_lobby)
    a, b, c = team_players(lobby, 1)
    sock_b = lobby.player_socket(live, b.player)
    sock_c = lobby.player_socket(live, c.player)
    for sock in (sock_b, sock_c):
        sock.wait_for_type("FLOOR_STATE")
    a.hint(1)
    for sock in (sock_b, sock_c):
        event = sock.wait_for_type("HINT_USED")["payload"]
        assert event["level"] == 1 and event["penalty_seconds"] == 30 and event["hints_remaining"] == 3
        assert "player_id" not in event and event["text"] == LADDER["candle"][0]
        state = sock.wait_for(lambda m: m["type"] == "FLOOR_STATE" and m["payload"]["hints"]["used"] == 1)["payload"]
        assert state["timer"]["penalty_seconds"] == 30


def test_hints_and_penalties_are_per_team(make_lobby, db, live):
    lobby = start_running(make_lobby)
    a1, *_ = team_players(lobby, 1)
    a2, b2, _ = team_players(lobby, 2)
    other = lobby.player_socket(live, b2.player)
    other.wait_for_type("FLOOR_STATE")
    other.collect(0.3)
    deadline2 = reload(db, Team, team_id(lobby, 2)).deadline_at
    a1.hint(4)
    assert a2.state()["hints"]["used"] == 0 and a2.state()["hints"]["remaining"] == 4
    assert reload(db, Team, team_id(lobby, 2)).deadline_at == deadline2
    assert a2.hint(4).status_code == 200  # team 2 still owns hint 4
    types = [m["type"] for m in other.collect(0.8) if isinstance(m, dict)]
    assert types.count("HINT_USED") == 1  # only its own team's hint, never team 1's


# ---------------------------------------------------------------- expiration
def expire(db, lobby, slot):
    team = db.get(Team, team_id(lobby, slot))
    team.deadline_at = utcnow() - timedelta(seconds=1)
    db.commit()


def test_an_expired_team_is_eliminated_and_cannot_continue(make_lobby, db):
    lobby = start_running(make_lobby)
    a, b, _ = team_players(lobby, 1)
    a.investigate("candle")
    expire(db, lobby, 1)
    first = a.answer("candle", "3")  # even a correct answer arrives too late
    assert first.status_code == 409 and error_code(first) == "TEAM_ELIMINATED"
    team = reload(db, Team, team_id(lobby, 1))
    assert team.status == "ELIMINATED" and team.finished_at is not None
    assert error_code(b.investigate("candle")) == "TEAM_ELIMINATED"  # the lock vanished, but nobody can act
    assert error_code(a.hint(1)) == "TEAM_ELIMINATED"
    assert error_code(a.vault(CODE)) == "TEAM_ELIMINATED"
    assert error_code(a.heartbeat("candle")) == "NOT_INVESTIGATING"
    state = b.state()
    assert state["team"]["status"] == "ELIMINATED" and state["timer"]["expired"] is True
    assert state["room"]["objects"][0]["status"] != "SOLVED"  # nothing was credited


def test_expiry_does_not_touch_other_teams(make_lobby, db):
    lobby = start_running(make_lobby)
    a1, *_ = team_players(lobby, 1)
    a2, *_ = team_players(lobby, 2)
    expire(db, lobby, 1)
    assert error_code(a1.investigate("candle")) == "TEAM_ELIMINATED"
    assert a2.investigate("candle").status_code == 200
    assert a2.state()["team"]["status"] == "PLAYING"


def test_the_sweeper_eliminates_overdue_teams_and_tells_the_team(make_lobby, app, db, live):
    lobby = start_running(make_lobby)
    a, b, _ = team_players(lobby, 1)
    sock = lobby.player_socket(live, b.player)
    sock.wait_for_type("FLOOR_STATE")
    expire(db, lobby, 1)

    out = service.sweep(app.state.db.session_factory, app.state.game)
    assert out["eliminated"] == [team_id(lobby, 1)]
    assert service.sweep(app.state.db.session_factory, app.state.game)["eliminated"] == []  # only once
    assert sock.wait_for_type("TEAM_ELIMINATED")["payload"]["team_name"]
    state = sock.wait_for(lambda m: m["type"] == "FLOOR_STATE" and m["payload"]["team"]["status"] == "ELIMINATED")
    assert state["payload"]["timer"]["expired"] is True
    assert reload(db, Team, team_id(lobby, 2)).status == "PLAYING"


def test_a_hint_penalty_can_not_be_used_to_buy_time(make_lobby, db):
    """The penalty must never move the deadline LATER (it would turn a hint into a bonus)."""
    lobby = start_running(make_lobby)
    a, *_ = team_players(lobby, 1)
    before = reload(db, Team, team_id(lobby, 1)).deadline_at
    a.hint(4)
    assert reload(db, Team, team_id(lobby, 1)).deadline_at < before
