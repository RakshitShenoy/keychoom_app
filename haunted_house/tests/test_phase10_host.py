"""Phase 10: the host dashboard (security, live updates), pause/resume, in-game kick, the winner view."""
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.db.models import Game, HintUsage, Player, Team
from app.game import service
from app.timers.clock import utcnow
from app.timers.deadline import team_deadline_for
from play_support import (
    CODE, final_code, load_library, make_settings, play_whole_building, reload, solve_floor,
    start_running, team_players, error_code,
)

LIBRARY = load_library()


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


def dash(lobby):
    response = lobby.host.get(lobby.url("/play/host"))
    assert response.status_code == 200, response.text
    return response.json()


def team_row(d, slot_name):
    return next(t for t in d["teams"] if t["name"] == slot_name)


# ================================================================ security
def test_only_the_games_host_may_use_the_dashboard_and_its_controls(make_lobby, second_host, harness):
    lobby = start_running(make_lobby)
    player = team_players(lobby, 1)[0]
    stranger = TestClient(lobby.app)
    other = harness(second_host, second_host.post("/api/games", json={"team_count": 3}).json())
    for method, suffix in (("get", "/play/host"), ("post", "/pause"), ("post", "/resume")):
        url = lobby.url(suffix if suffix == "/play/host" else suffix)
        url = lobby.url(suffix)
        if suffix == "/play/host":
            pass
        # a player of the game: forbidden
        r = getattr(player.client, method)(url) if method == "get" else player.client.post(url, json={})
        assert r.status_code == 403 and error_code(r) == "HOST_ONLY", (suffix, r.text)
        # nobody logged in
        r = getattr(stranger, method)(url) if method == "get" else stranger.post(url, json={})
        assert r.status_code == 401
        # the host of ANOTHER game
        r = second_host.get(url) if method == "get" else second_host.post(url, json={})
        assert r.status_code == 403 and error_code(r) == "WRONG_GAME"
    assert dash(lobby)["paused"] is False  # nothing above had any effect
    assert other.code != lobby.code


def test_a_player_cannot_pause_by_sending_extra_fields_or_over_the_websocket(make_lobby, live):
    lobby = start_running(make_lobby)
    player = team_players(lobby, 1)[0]
    sock = lobby.player_socket(live, player.player)
    sock.wait_for_type("LOBBY_STATE")
    for attack in ({"type": "PAUSE_GAME"}, {"type": "RESUME_GAME"}, {"type": "KICK_PLAYER", "player_id": "x"}):
        sock.send(attack)
        assert sock.wait_for_type("ERROR")["code"] == "UNSUPPORTED_MESSAGE"
    r = lobby.host.post(lobby.url("/pause"), json={"paused": False, "game_id": "x"})
    assert r.status_code == 422  # extra fields are refused, not honoured
    assert dash(lobby)["paused"] is False


# ================================================================ what the host sees
def test_host_sees_every_team_with_the_full_overview(make_lobby):
    lobby = start_running(make_lobby, team_count=3)
    d = dash(lobby)
    assert d["game_status"] == "RUNNING" and d["paused"] is False and d["total_floors"] == 8
    assert len(d["teams"]) == 3 and d["players"] == {"total": 9, "connected": 0}
    t = d["teams"][0]
    for key in ("name", "icon", "status", "leader", "members", "players", "floor", "room", "task", "where",
                "fragments", "fragment_flags", "hints_used", "hints_remaining", "penalty_seconds",
                "deadline_at", "seconds_remaining", "elapsed_seconds", "connected", "investigating",
                "required_solved", "required_total", "at_final_door"):
        assert key in t, key
    assert t["status"] == "PLAYING" and t["floor"] == 1 and t["players"] == 3 and len(t["members"]) == 3
    assert t["leader"] == lobby.players[0].name and t["hints_remaining"] == 4 and t["fragment_flags"] == [False] * 8
    assert t["room"]["title"] and t["task"] and "Floor 1" in t["where"]


def test_dashboard_tracks_progress_and_never_leaks_secrets(make_lobby):
    lobby = start_running(make_lobby, team_count=3)
    players = team_players(lobby, 1)
    name = lobby.team(1)["name"]
    players[0].investigate("candle")
    row = team_row(dash(lobby), name)
    assert row["investigating"] == [{"object_id": "candle", "label": row["investigating"][0]["label"], "player": players[0].player.name}]
    assert "investigating" in row["where"]
    players[0].answer("candle", "3")
    players[1].hint(1)
    d = dash(lobby)
    row = team_row(d, name)
    assert row["required_solved"] == 1 and row["hints_used"] == 1 and row["hints_remaining"] == 3
    assert row["penalty_seconds"] == 30
    text = str(d)
    for secret in (CODE, "K7R4", '"answer"', "vault_code", "reveal_text"):
        assert secret not in text
    hint_text = players[1].state()["hints"]["revealed"][0]["text"]
    assert hint_text and hint_text not in text  # the host sees that a hint was used, not its text


def test_floor_progress_and_fragments_show_on_the_dashboard(make_lobby):
    lobby = start_running(make_lobby, team_count=3)
    players = team_players(lobby, 1)
    solve_floor(players, LIBRARY)
    d = dash(lobby)
    a, b = team_row(d, lobby.team(1)["name"]), team_row(d, lobby.team(2)["name"])
    assert a["floor"] == 2 and a["fragments"] == 1 and a["fragment_flags"][0] is True
    assert b["floor"] == 1 and b["fragments"] == 0  # another team is unaffected
    assert any(f["type"] == "FRAGMENT_AWARDED" for f in d["feed"]) or any(f["type"] == "FLOOR_ENTERED" for f in d["feed"])


# ================================================================ live updates over the WebSocket
def test_host_dashboard_is_pushed_live_and_players_never_receive_it(make_lobby, live):
    lobby = start_running(make_lobby, team_count=3)
    players = team_players(lobby, 1)
    host = lobby.host_socket(live)
    first = host.wait_for_type("HOST_DASHBOARD")
    assert len(first["payload"]["teams"]) == 3 and isinstance(first["version"], int)
    psock = lobby.player_socket(live, players[0].player)
    psock.wait_for_type("LOBBY_STATE")
    host.collect(0.8)

    players[0].investigate("candle")
    players[0].answer("candle", "3")
    name = lobby.team(1)["name"]
    updated = host.wait_for(lambda m: m["type"] == "HOST_DASHBOARD" and team_row(m["payload"], name)["required_solved"] == 1)
    assert updated["version"] > first["version"]
    # the team's own player got its floor state, but never the host's dashboard
    psock.expect_no_type("HOST_DASHBOARD", seconds=1.0)


def test_dashboard_updates_for_hints_eliminations_and_presence(make_lobby, live, db, app):
    lobby = start_running(make_lobby, team_count=3)
    players = team_players(lobby, 1)
    host = lobby.host_socket(live)
    host.wait_for_type("HOST_DASHBOARD")
    name = lobby.team(1)["name"]

    players[0].hint(1)
    host.wait_for(lambda m: m["type"] == "HOST_DASHBOARD" and team_row(m["payload"], name)["hints_used"] == 1)

    sock = lobby.player_socket(live, players[0].player)  # a player connecting
    host.wait_for(lambda m: m["type"] == "HOST_DASHBOARD" and team_row(m["payload"], name)["connected"] == 1)
    sock.close()  # ... and disconnecting
    host.wait_for(lambda m: m["type"] == "HOST_DASHBOARD" and team_row(m["payload"], name)["connected"] == 0)

    team2 = reload(db, Team, lobby.team(2)["team_id"])  # a team running out of time
    team2.deadline_at = utcnow() - timedelta(seconds=1)
    db.commit()
    service.sweep(app.state.db.session_factory, app.state.game)
    host.wait_for(lambda m: m["type"] == "HOST_DASHBOARD" and team_row(m["payload"], lobby.team(2)["name"])["status"] == "ELIMINATED")


def test_host_refresh_restores_the_dashboard(make_lobby, live, copy_cookies):
    lobby = start_running(make_lobby)
    first = lobby.host_socket(live)
    first.wait_for_type("HOST_DASHBOARD")
    first.close()
    reloaded = TestClient(lobby.app)  # a refreshed tab: same cookie, new connection
    copy_cookies(lobby.host, reloaded)
    sock = live(reloaded, f"/ws/games/{lobby.code}?role=host")
    again = sock.wait_for_type("HOST_DASHBOARD")
    assert len(again["payload"]["teams"]) == 3 and reloaded.get("/api/me").json()["host"]["game_code"] == lobby.code
    sock.send({"type": "REQUEST_STATE"})
    sock.wait_for_type("LOBBY_STATE")


# ================================================================ pause / resume
def test_pause_rejects_every_gameplay_action_on_the_server(make_lobby):
    lobby = start_running(make_lobby)
    players = team_players(lobby, 1)
    players[0].investigate("candle")  # holds a lease when the pause begins
    assert lobby.host.post(lobby.url("/pause"), json={}).json()["paused"] is True

    p = players[1]
    attempts = {
        "investigate": p.investigate("vent"), "answer": players[0].answer("candle", "3"),
        "hint": p.hint(1), "vault": p.vault(CODE), "advance": p.call("POST", "/floor/next"),
        "door": p.call("POST", "/final-door", {"code": "AAAAAAAA"}),
        "quiz": p.quiz_start("candle"), "dare": p.dare_confirm("candle"),
    }
    for what, response in attempts.items():
        assert response.status_code == 409 and error_code(response) == "GAME_PAUSED", (what, response.text)
    state = p.state()  # reading is still allowed, and says so
    assert state["paused"] is True and state["timer"]["paused"] is True and state["game"]["paused"] is True
    assert state["hints"]["available"] is False and state["final_door"]["status"] == "LOCKED"
    assert lobby.player_socket and players[0].obj("candle")["status"] != "SOLVED"


def test_pause_freezes_the_clock_and_resume_gives_the_time_back(make_lobby, db, app):
    lobby = start_running(make_lobby)
    players = team_players(lobby, 1)
    runtime, factory = app.state.game, app.state.db.session_factory
    team = reload(db, Team, lobby.team(1)["team_id"])
    game = reload(db, Game, lobby.game["game_id"])
    before = team.deadline_at
    t0 = utcnow()

    service.pause_game(db, runtime, game.id, now=t0)
    left_at_pause = players[0].state()["timer"]["seconds_remaining"]
    # an hour passes while paused: nobody is eliminated and nothing expires
    result = service.sweep(factory, runtime, now=t0 + timedelta(hours=5))
    assert result["eliminated"] == [] and result["jumpscares"] == 0
    assert reload(db, Team, team.id).status == "PLAYING"
    assert abs(players[0].state()["timer"]["seconds_remaining"] - left_at_pause) < 2  # frozen

    service.resume_game(db, runtime, game.id, now=t0 + timedelta(seconds=300))
    team, game = reload(db, Team, team.id), reload(db, Game, game.id)
    assert game.paused_at is None and game.paused_seconds_total == 300
    assert team.deadline_at == before + timedelta(seconds=300)  # exactly the pause length
    # a hint taken after the pause recomputes the same deadline, pause included
    resp = players[0].hint(1)
    assert resp.status_code == 200
    team = reload(db, Team, team.id)
    assert team.deadline_at == team_deadline_for(game, 30) == before + timedelta(seconds=270)
    assert players[0].investigate("candle").status_code == 200  # play continues


def test_leases_and_quizzes_survive_a_pause(make_lobby, db, app):
    lobby = start_running(make_lobby)
    players = team_players(lobby, 1)
    runtime = app.state.game
    team_id = lobby.team(1)["team_id"]
    assert players[0].investigate("candle").status_code == 200
    t0 = utcnow()
    service.pause_game(db, runtime, lobby.game["game_id"], now=t0)
    assert service.sweep(app.state.db.session_factory, runtime, now=t0 + timedelta(minutes=10))["released"] == 0
    service.resume_game(db, runtime, lobby.game["game_id"], now=t0 + timedelta(minutes=10))
    holder = runtime.leases.holder(team_id, "candle", t0 + timedelta(minutes=10, seconds=30))
    assert holder is not None and holder.player_id == players[0].player.id  # not stolen, not expired


def test_pause_and_resume_rules(make_lobby):
    lobby = make_lobby()
    assert error_code(lobby.host.post(lobby.url("/pause"), json={})) == "GAME_NOT_RUNNING"  # not started yet
    lobby = start_running(make_lobby)
    assert error_code(lobby.host.post(lobby.url("/resume"), json={})) == "NOT_PAUSED"
    assert lobby.host.post(lobby.url("/pause"), json={}).status_code == 200
    assert error_code(lobby.host.post(lobby.url("/pause"), json={})) == "ALREADY_PAUSED"
    assert lobby.host.post(lobby.url("/resume"), json={}).json()["paused"] is False
    assert error_code(lobby.host.post(lobby.url("/resume"), json={})) == "NOT_PAUSED"


def test_simultaneous_pause_requests_pause_exactly_once(make_lobby, db):
    lobby = start_running(make_lobby)
    barrier = threading.Barrier(4)

    def go(_):
        barrier.wait()
        return lobby.host.post(lobby.url("/pause"), json={}).status_code

    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(go, range(4)))
    assert sorted(results) == [200, 409, 409, 409]
    assert reload(db, Game, lobby.game["game_id"]).paused_at is not None


def test_pause_is_announced_to_players_and_host_without_a_refresh(make_lobby, live):
    lobby = start_running(make_lobby)
    players = team_players(lobby, 1)
    other = team_players(lobby, 2)[0]
    host = lobby.host_socket(live)
    p1, p2 = lobby.player_socket(live, players[0].player), lobby.player_socket(live, other.player)
    for s in (host, p1, p2):
        s.wait_for_type("LOBBY_STATE")
        s.collect(0.6)
    lobby.host.post(lobby.url("/pause"), json={})
    for s in (p1, p2):
        assert s.wait_for_type("GAME_PAUSED")
        s.wait_for(lambda m: m["type"] == "FLOOR_STATE" and m["payload"]["paused"] is True)
    host.wait_for(lambda m: m["type"] == "HOST_DASHBOARD" and m["payload"]["paused"] is True)
    lobby.host.post(lobby.url("/resume"), json={})
    p1.wait_for(lambda m: m["type"] == "FLOOR_STATE" and m["payload"]["paused"] is False)
    host.wait_for(lambda m: m["type"] == "HOST_DASHBOARD" and m["payload"]["paused"] is False)


def test_pause_survives_a_server_restart(make_lobby, settings, copy_cookies):
    from app.main import create_app

    lobby = start_running(make_lobby)
    lobby.host.post(lobby.url("/pause"), json={})
    restarted = create_app(settings)
    with TestClient(restarted) as host2, TestClient(restarted) as player2:
        copy_cookies(lobby.host, host2)
        copy_cookies(lobby.players[0].client, player2)
        assert host2.get(lobby.url("/play/host")).json()["paused"] is True
        r = player2.post(lobby.url("/play/objects/candle/investigate"))
        assert r.status_code == 409 and error_code(r) == "GAME_PAUSED"


# ================================================================ kicking during the game
def test_host_can_remove_a_player_from_a_running_game(make_lobby):
    lobby = start_running(make_lobby)
    players = team_players(lobby, 1)
    victim = players[2]
    assert victim.investigate("vent").status_code != 500
    team_name = lobby.team(1)["name"]
    # players cannot kick or leave a running game
    assert players[0].client.post(lobby.url(f"/players/{victim.player.id}/kick")).status_code == 403
    assert victim.client.post(lobby.url("/leave")).status_code == 409

    assert lobby.kick(victim.player).status_code == 200
    assert victim.client.get(lobby.url("/play/state")).status_code == 401  # the session is gone
    assert team_row(dash(lobby), team_name)["players"] == 2
    assert players[0].state()["members"] and len(players[0].state()["members"]) == 2
    # the team goes on; an object the kicked player was holding is free again
    assert players[0].investigate("candle").status_code == 200


# ================================================================ the winner view
def test_winner_view_has_time_state_and_only_one_winner(make_lobby, db):
    lobby = start_running(make_lobby, team_count=3)
    teams = [team_players(lobby, s) for s in (1, 2)]
    plays = [play_whole_building(t, LIBRARY) for t in teams]
    assert teams[0][0].call("POST", "/final-door", {"code": final_code(plays[0])}).status_code == 200
    r = teams[1][0].call("POST", "/final-door", {"code": final_code(plays[1])})
    assert r.status_code == 409 and error_code(r) == "GAME_OVER"

    d = dash(lobby)
    assert d["game_status"] == "FINISHED" and d["winner"]["team_name"] == lobby.team(1)["name"]
    assert isinstance(d["winner"]["completion_seconds"], int) and d["winner"]["completion_seconds"] >= 0
    assert [t["status"] for t in d["teams"]].count("ESCAPED") == 1
    assert team_row(d, lobby.team(1)["name"])["status"] == "ESCAPED"
    final_state = dash(lobby)  # final state is preserved on every later read
    assert final_state["winner"] == d["winner"]
    assert error_code(lobby.host.post(lobby.url("/pause"), json={})) == "GAME_NOT_RUNNING"  # over: cannot pause


def test_completion_time_does_not_count_the_paused_time(make_lobby, db, app):
    lobby = start_running(make_lobby, team_count=3)
    players = team_players(lobby, 1)
    played = play_whole_building(players, LIBRARY)
    runtime = app.state.game
    game_id = lobby.game["game_id"]
    # pretend the game has been running for 20 minutes (the pause below is 10 of them)
    game = reload(db, Game, game_id)
    game.started_at = game.started_at - timedelta(seconds=1200)
    for team in db.query(Team).filter_by(game_id=game_id):
        team.deadline_at = team.deadline_at - timedelta(seconds=1200)
    db.commit()
    t0 = utcnow()
    service.pause_game(db, runtime, game_id, now=t0)
    service.resume_game(db, runtime, game_id, now=t0 + timedelta(seconds=600))
    assert players[0].call("POST", "/final-door", {"code": final_code(played)}).status_code == 200
    d = dash(lobby)
    game = reload(db, Game, game_id)
    raw = (game.ended_at - game.started_at).total_seconds()
    assert game.paused_seconds_total == 600
    assert d["winner"]["completion_seconds"] == int(raw) - 600 and d["winner"]["completion_seconds"] > 500
