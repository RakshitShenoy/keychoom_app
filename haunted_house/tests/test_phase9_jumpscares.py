"""Phase 9: server-controlled jumpscares, trap objects, cooldowns, isolation and 'presentation only' safety."""
import copy
from datetime import timedelta
from pathlib import Path

import pytest

from app.db.models import Game, JumpscareEvent, Team
from app.game import service
from app.jumpscares import assets
from app.jumpscares.service import END_FRACTION, START_FRACTION, plan_times
from app.lobby.events import LobbyEventType
from app.timers.clock import utcnow
from play_support import (
    climb_to_floor, load_library, make_settings, preset_by_title, reload, start_running, team_players,
)

LIBRARY = load_library()
STATIC = Path(__file__).resolve().parent.parent / "static"


@pytest.fixture
def settings(tmp_path, request):
    extra = getattr(request, "param", {})
    return make_settings(tmp_path, **{"jumpscare_cooldown_seconds": 90.0, "jumpscares_per_team": 3, **extra})


def park_other_teams(db, lobby, keep_slot):
    """Move every OTHER team's scheduled scares far into the future so a test can watch one team alone."""
    for slot in (1, 2, 3):
        if slot != keep_slot:
            for row in db.query(JumpscareEvent).filter_by(team_id=tid(lobby, slot)).all():
                row.scheduled_at = row.scheduled_at + timedelta(days=2)
    db.commit()


def tid(lobby, slot):
    return lobby.team(slot)["team_id"]


def sweep(app, now):
    return service.sweep(app.state.db.session_factory, app.state.game, now)


def capture(app):
    seen = []
    app.state.lobby_bus.subscribe(lambda e: seen.append(e))
    return seen


def scares(seen, team=None):
    return [e for e in seen if e.type is LobbyEventType.JUMPSCARE and (team is None or e.team_id == team)]


def comparable(state):
    """The part of a floor state that gameplay owns (clock readings excluded)."""
    out = copy.deepcopy(state)
    for key in ("server_now", "seconds_remaining"):
        out["timer"].pop(key, None)
    return out


# ================================================================ planning (pure)
def test_the_plan_is_random_but_deterministic_spread_out_and_inside_the_window():
    start = utcnow()
    plan = plan_times("g", "t", start, 3600, 3, 90.0)
    assert plan == plan_times("g", "t", start, 3600, 3, 90.0)
    assert plan != plan_times("g", "other-team", start, 3600, 3, 90.0)
    offsets = [(when - start).total_seconds() for when, _ in plan]
    assert offsets == sorted(offsets) and len(offsets) == 3
    assert all(3600 * START_FRACTION <= o <= 3600 * END_FRACTION for o in offsets)
    assert all(b - a >= 90.0 for a, b in zip(offsets, offsets[1:]))  # cooldown respected inside the plan
    assert len({asset for _, asset in plan}) == 3  # different artwork each time
    assert all(assets.is_known(a) for _, a in plan)
    assert plan_times("g", "t", start, 3600, 0, 90.0) == []


def test_short_games_still_get_at_least_two_scares():
    plan = plan_times("g", "t", utcnow(), 300, 2, 90.0)
    assert len(plan) == 2


def test_every_registered_asset_exists_and_is_displayed_for_about_five_seconds():
    assert len(assets.SCARES) >= 5 and len(set(assets.SCARE_IDS)) == len(assets.SCARE_IDS)
    for scare in assets.SCARES:
        assert (STATIC / "img" / "jumpscares" / scare["file"]).is_file()
        assert assets.asset_public(scare["id"], 5000)["duration_ms"] == 5000


def test_starting_the_game_schedules_scares_for_every_team_without_firing_any(make_lobby, db):
    lobby = start_running(make_lobby)
    for slot in (1, 2, 3):
        rows = db.query(JumpscareEvent).filter_by(team_id=tid(lobby, slot)).all()
        assert len(rows) == 3 and all(r.fired_at is None and r.kind == "IMAGE" for r in rows)
    game = reload(db, Game, lobby.game["game_id"])
    assert all(game.started_at < r.scheduled_at < game.started_at + timedelta(seconds=game.base_duration_seconds)
               for r in db.query(JumpscareEvent).all())


@pytest.mark.parametrize("settings", [{"jumpscares_enabled": False}], indirect=True)
def test_jumpscares_can_be_switched_off_for_a_venue(make_lobby, app, db):
    lobby = start_running(make_lobby)
    assert db.query(JumpscareEvent).count() == 0
    players, trap = reach_floor_two(lobby)
    assert players[0].investigate(trap["id"]).json()["result"]["jumpscare"] is None  # flavour text only
    assert sweep(app, utcnow() + timedelta(hours=1))["jumpscares"] == 0


# ================================================================ firing
def test_a_due_scare_fires_once_to_its_own_team_only(make_lobby, app, db):
    lobby = start_running(make_lobby)
    seen = capture(app)
    park_other_teams(db, lobby, 1)
    row = db.query(JumpscareEvent).filter_by(team_id=tid(lobby, 1)).order_by(JumpscareEvent.scheduled_at).first()
    assert sweep(app, row.scheduled_at - timedelta(seconds=1))["jumpscares"] == 0  # not yet
    assert sweep(app, row.scheduled_at + timedelta(seconds=1))["jumpscares"] == 1
    fired = scares(seen)
    assert len(fired) == 1 and fired[0].team_id == tid(lobby, 1)  # team 2 and 3 are untouched
    payload = fired[0].payload
    assert payload["asset_id"] == row.asset_id and payload["duration_ms"] == 5000 and payload["url"].startswith("/static/img/jumpscares/")
    assert "only_player_id" not in payload and "answer" not in str(payload)
    assert reload(db, JumpscareEvent, row.id).fired_at is not None
    assert sweep(app, row.scheduled_at + timedelta(seconds=2))["jumpscares"] == 0  # never twice


def test_the_cooldown_stops_back_to_back_scares_and_the_second_one_waits(make_lobby, app, db):
    lobby = start_running(make_lobby)
    seen = capture(app)
    park_other_teams(db, lobby, 1)
    rows = db.query(JumpscareEvent).filter_by(team_id=tid(lobby, 1)).order_by(JumpscareEvent.scheduled_at).all()
    t0 = utcnow() + timedelta(seconds=5)
    rows[0].scheduled_at = rows[1].scheduled_at = t0  # two scares due at the very same moment
    db.commit()
    assert sweep(app, t0 + timedelta(seconds=1))["jumpscares"] == 1
    assert len(scares(seen)) == 1
    db.expire_all()
    pending = [reload(db, JumpscareEvent, r.id) for r in rows[:2] if reload(db, JumpscareEvent, r.id).fired_at is None]
    assert len(pending) == 1 and pending[0].scheduled_at >= t0 + timedelta(seconds=89)  # pushed back by the cooldown
    assert sweep(app, t0 + timedelta(seconds=30))["jumpscares"] == 0
    assert sweep(app, t0 + timedelta(seconds=95))["jumpscares"] >= 1


def test_each_team_gets_at_least_two_scares_over_a_complete_game(make_lobby, app, db):
    lobby = start_running(make_lobby)
    game = reload(db, Game, lobby.game["game_id"])
    seen = capture(app)
    now = game.started_at
    while now < game.started_at + timedelta(seconds=game.base_duration_seconds) - timedelta(seconds=5):
        now += timedelta(seconds=30)
        sweep(app, now)
        for team in db.query(Team).filter_by(game_id=game.id):  # keep every team alive for this timeline test
            team.deadline_at = now + timedelta(hours=3)
        db.commit()
    for slot in (1, 2, 3):
        mine = scares(seen, tid(lobby, slot))
        assert len(mine) >= 2
        times = sorted(db.query(JumpscareEvent.fired_at).filter_by(team_id=tid(lobby, slot)).all())
        assert all((b[0] - a[0]).total_seconds() >= 90 for a, b in zip(times, times[1:]))  # cooldown held all game


def test_a_scare_never_touches_gameplay_state(make_lobby, app, db):
    lobby = start_running(make_lobby)
    a, b, _ = team_players(lobby, 1)
    a.solve("candle")
    a.hint(1)
    a.investigate("painting")
    before = comparable(a.state())
    park_other_teams(db, lobby, 1)
    row = db.query(JumpscareEvent).filter_by(team_id=tid(lobby, 1)).order_by(JumpscareEvent.scheduled_at).first()
    from app.jumpscares import service as scare_service  # fire ONLY the scare (a full sweep would also age leases in virtual time)
    assert scare_service.fire_due(db, app.state.game, row.scheduled_at + timedelta(seconds=1)) == 1
    after = comparable(a.state())
    assert after == before  # objects, leases, vault, clues, hints, penalties, deadline: all identical
    assert a.answer("painting", "maid").json()["result"]["result"] == "CORRECT"  # and play simply continues


def test_no_scares_for_eliminated_teams_or_after_the_game_ended(make_lobby, app, db):
    lobby = start_running(make_lobby)
    seen = capture(app)
    team = reload(db, Team, tid(lobby, 1))
    team.status = "ELIMINATED"
    db.commit()
    rows = db.query(JumpscareEvent).filter_by(team_id=team.id).all()
    assert sweep(app, max(r.scheduled_at for r in rows) + timedelta(seconds=1))["jumpscares"] <= 2  # only the OTHER teams' scares
    assert scares(seen, team.id) == []
    game = reload(db, Game, lobby.game["game_id"])
    game.status = "FINISHED"
    db.commit()
    assert sweep(app, utcnow() + timedelta(hours=2))["jumpscares"] == 0


# ================================================================ trap objects
def reach_floor_two(lobby):
    players = team_players(lobby, 1)
    climb_to_floor(players, LIBRARY, 2)
    state = players[0].state()
    preset = preset_by_title(LIBRARY, state["room"]["title"])
    trap = next(o for o in preset["objects"] if o["type"] == "trap")
    return players, trap


def test_a_trap_object_scares_only_the_player_who_touched_it(make_lobby, app, db):
    lobby = start_running(make_lobby)
    players, trap = reach_floor_two(lobby)
    a, b, _ = players
    seen = capture(app)
    before = comparable(b.state())
    response = a.investigate(trap["id"])
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["result"] == "TRAP" and result["message"] == trap["reward_text"]
    assert result["jumpscare"]["duration_ms"] == 5000 and "only_player_id" not in result["jumpscare"]
    fired = scares(seen)
    assert len(fired) == 1 and fired[0].payload["only_player_id"] == lobby.players[0].id and fired[0].team_id == tid(lobby, 1)
    row = db.query(JumpscareEvent).filter_by(team_id=tid(lobby, 1), kind="TRAP").one()
    assert row.target_player_id == lobby.players[0].id and row.fired_at is not None
    # presentation only: no lease, no object state, nothing solved or reset
    assert comparable(b.state()) == before
    assert a.state()["my_investigation"] is None and a.obj(trap["id"])["status"] == "AVAILABLE"


def test_traps_respect_the_team_cooldown_and_never_spam(make_lobby, app):
    lobby = start_running(make_lobby)
    players, trap = reach_floor_two(lobby)
    a, b, _ = players
    first = a.investigate(trap["id"]).json()["result"]
    second = b.investigate(trap["id"]).json()["result"]  # a teammate touches it right away
    assert first["jumpscare"] is not None
    assert second["result"] == "TRAP" and second["jumpscare"] is None  # still in cooldown: just the flavour text


def test_a_trap_does_not_block_or_complete_anything(make_lobby):
    lobby = start_running(make_lobby)
    players, trap = reach_floor_two(lobby)
    a, b, c = players
    a.investigate(trap["id"])
    state = a.state()
    assert state["progress"]["required_solved"] == 0 and state["room"]["vault"]["status"] == "LOCKED"
    assert state["hints"]["used"] == 0 and state["timer"]["penalty_seconds"] == 0


def test_trap_events_reach_the_right_sockets(make_lobby, live):
    lobby = start_running(make_lobby)
    players, trap = reach_floor_two(lobby)
    mine = lobby.player_socket(live, lobby.players[0])
    mate = lobby.player_socket(live, lobby.players[1])
    foreign = lobby.player_socket(live, lobby.players[3])
    host = lobby.host_socket(live)
    for sock in (mine, mate, foreign, host):
        sock.wait_for_type("LOBBY_STATE")
        sock.collect(0.5)
    players[0].investigate(trap["id"])
    got = mine.wait_for_type("JUMPSCARE")["payload"]
    assert got["asset_id"] and "only_player_id" not in got and "team_id" not in got
    assert host.wait_for_type("JUMPSCARE")["payload"]["team_id"] == tid(lobby, 1)
    mate.expect_no_type("JUMPSCARE", 0.6)
    foreign.expect_no_type("JUMPSCARE", 0.3)


def test_scheduled_scares_reach_the_whole_team_but_nobody_else(make_lobby, app, db, live):
    lobby = start_running(make_lobby)
    team_socks = [lobby.player_socket(live, p) for p in lobby.players[0:3]]
    foreign = lobby.player_socket(live, lobby.players[4])
    for sock in (*team_socks, foreign):
        sock.wait_for_type("LOBBY_STATE")
        sock.collect(0.5)
    park_other_teams(db, lobby, 1)
    row = db.query(JumpscareEvent).filter_by(team_id=tid(lobby, 1)).order_by(JumpscareEvent.scheduled_at).first()
    sweep(app, row.scheduled_at + timedelta(seconds=1))
    for sock in team_socks:
        assert sock.wait_for_type("JUMPSCARE")["payload"]["asset_id"] == row.asset_id
    foreign.expect_no_type("JUMPSCARE", 0.6)


def test_jumpscare_settings_are_validated():
    with pytest.raises(ValueError):
        make_settings(Path("/tmp"), jumpscare_display_ms=100)
