"""Phase 13: host-selected floor count, a hidden random floor order per game, and host "Play as Player".

This module is marked ``random_floors``: games here get the REAL secure shuffle (the other modules pin
the order to 1..N so their room-specific assertions stay valid). Nothing below depends on one particular
random order: tests check validity, uniqueness, persistence, progression and what players can see.
"""
import json
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.db.models import Game, GameEvent, Player, Team, TeamFloorState
from app.game import presets as pr
from conftest import PlayerHandle
from play_support import (
    Play, error_code, final_code, load_library, make_settings, play_whole_building, reload, solve_floor,
    team_players,
)

pytestmark = pytest.mark.random_floors
LIBRARY = load_library()


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def lobby_for(client, harness):
    def _make(team_count=3, **config):
        response = client.post("/api/games", json={"team_count": team_count, **config})
        assert response.status_code == 201, response.text
        return harness(client, response.json())

    return _make


def fill(lobby, sizes):
    lobby.join_many(sum(sizes))
    lobby.start_formation()
    cursor = 0
    for slot, size in enumerate(sizes, start=1):
        block = lobby.players[cursor : cursor + size]
        cursor += size
        assert lobby.assign(slot, block[0]).status_code == 200
        code = lobby.team_code(slot)
        for member in block[1:]:
            assert lobby.join_team(member, code).status_code == 200


def started(lobby_for, floors=None, sizes=(1, 1, 1), **config):
    lobby = lobby_for(3, **({"floor_count": floors} if floors else {}), **config)
    fill(lobby, list(sizes))
    assert lobby.lock().status_code == 200
    assert lobby.host.post(lobby.url("/start")).status_code == 200
    return lobby


def game_row(db, lobby):
    return reload(db, Game, lobby.game["game_id"])


def order_of(db, lobby):
    return list(game_row(db, lobby).floor_order)


def preset_floors(db, lobby, slot):
    """Internal floor of the room each position of ONE team used (server-side view, for assertions)."""
    team_id = lobby.team(slot)["team_id"]
    db.expire_all()
    rows = db.scalars(select(TeamFloorState).where(TeamFloorState.team_id == team_id)
                      .order_by(TeamFloorState.floor_no)).all()
    return [LIBRARY.presets[r.preset_id]["floor"] for r in rows]


# ============================================================ unit: the generator
@pytest.mark.parametrize("count", range(3, 9))
def test_generator_returns_distinct_valid_floors(count):
    for _ in range(50):
        order = pr.generate_floor_order(count)
        assert len(order) == count == len(set(order)) and set(order) <= set(range(1, 9))


@pytest.mark.parametrize("count", [0, 2, 9, -1])
def test_generator_refuses_counts_outside_3_to_8(count):
    with pytest.raises(ValueError):
        pr.generate_floor_order(count)


def test_floor_order_of_ignores_a_corrupt_stored_order():
    class G:  # a tampered / legacy row never produces an out-of-range or duplicated sequence
        floor_count = 4

    for bad in (None, [1, 1, 2, 3], [1, 2, 3], [1, 2, 3, 99], "x", [True, 2, 3, 4]):
        G.floor_order = bad
        assert pr.floor_order_of(G) == [1, 2, 3, 4]
    G.floor_order = [8, 3, 5, 1]
    assert pr.floor_order_of(G) == [8, 3, 5, 1]


# ============================================================ floor count: creation (tests 1-10)
def test_default_game_has_eight_floors(client, db):  # 1
    body = client.post("/api/games", json={}).json()
    assert body["floor_count"] == 8 and body["host_plays"] is False
    row = db.get(Game, body["game_id"])
    assert row.floor_count == 8 and len(row.floor_order) == 8 and sorted(row.floor_order) == list(range(1, 9))


@pytest.mark.parametrize("count", [3, 4, 5, 6, 7, 8])  # 2-7, 13
def test_host_can_create_games_of_3_to_8_floors(client, db, count):
    response = client.post("/api/games", json={"floor_count": count})
    assert response.status_code == 201 and response.json()["floor_count"] == count
    row = db.get(Game, response.json()["game_id"])
    assert row.floor_count == count and len(row.floor_order) == count == len(set(row.floor_order))
    assert set(row.floor_order) <= set(range(1, 9))


@pytest.mark.parametrize("value", [2, 9, 0, -3, 100])  # 8, 9
def test_floor_counts_outside_3_to_8_are_rejected(client, db, value):
    response = client.post("/api/games", json={"floor_count": value})
    assert response.status_code == 422
    assert "floor_count" in [d["field"] for d in response.json()["error"]["details"]]
    assert db.query(Game).count() == 0


@pytest.mark.parametrize("value", ["5", "abc", 5.5, True, [5], {"n": 5}])  # 10
def test_non_numeric_floor_counts_are_rejected(client, db, value):
    assert client.post("/api/games", json={"floor_count": value}).status_code == 422
    assert db.query(Game).count() == 0


@pytest.mark.parametrize("extra", [{"floor_order": [1, 2, 3, 4, 5, 6, 7, 8]}, {"internal_floors": [1]}, {"is_host_player": True}])
def test_the_browser_cannot_choose_the_order(client, extra):  # 36
    assert client.post("/api/games", json=extra).status_code == 422


def test_host_plays_must_be_a_real_boolean(client):
    for bad in ("yes", 1, "true", 0):
        assert client.post("/api/games", json={"host_plays": bad}).status_code == 422
    assert client.post("/api/games", json={"host_plays": True}).json()["host_plays"] is True


# ============================================================ randomness + persistence (14-18, 35-36)
def test_new_games_get_independent_random_orders(client, db):  # 13, 14
    orders = set()
    for _ in range(30):
        body = client.post("/api/games", json={"floor_count": 8}).json()
        order = list(db.get(Game, body["game_id"]).floor_order)
        assert len(set(order)) == 8
        orders.add(tuple(order))
    assert len(orders) > 1  # 30 identical draws out of 40320 orders is not going to happen


def test_five_floor_games_pick_different_subsets(client, db):
    subsets = set()
    for _ in range(30):
        order = list(db.get(Game, client.post("/api/games", json={"floor_count": 5}).json()["game_id"]).floor_order)
        assert len(order) == len(set(order)) == 5
        subsets.add(frozenset(order))
    assert len(subsets) > 1


def test_order_and_count_survive_refresh_reconnect_and_restart(lobby_for, db, live, copy_cookies, settings):  # 15-18
    lobby = started(lobby_for, floors=5)
    before = order_of(db, lobby)
    first_rooms = [Play(lobby, p).state()["room"]["title"] for p in lobby.players]
    sock = lobby.player_socket(live, lobby.players[0])
    ws_state = sock.wait_for_type("FLOOR_STATE")["payload"]
    sock.close()
    for _ in range(3):  # refreshes: every kind of read
        lobby.host.get("/api/me"); lobby.host.get(lobby.url("/lobby"))
        for p in lobby.players:
            p.client.get("/api/me"); p.client.get(lobby.url("/lobby")); Play(lobby, p).state()
    again = lobby.player_socket(live, lobby.players[0]).wait_for_type("FLOOR_STATE")["payload"]  # ws reconnect
    assert again["room"]["title"] == ws_state["room"]["title"] == first_rooms[0]
    fresh = TestClient(lobby.app)  # a brand-new browser carrying the same cookie = player reconnect
    copy_cookies(lobby.players[1].client, fresh)
    assert fresh.get(lobby.url("/play/state")).json()["state"]["room"]["title"] == first_rooms[1]

    from app.main import create_app
    with TestClient(create_app(settings)) as restarted:  # server restart: state comes from the database
        copy_cookies(lobby.players[2].client, restarted)
        assert restarted.get(lobby.url("/play/state")).json()["state"]["game"]["total_floors"] == 5

    assert order_of(db, lobby) == before and game_row(db, lobby).floor_count == 5
    assert lobby.host.get("/api/me").json()["host"]["floor_count"] == 5
    assert lobby.lobby()["floor_count"] == 5 and lobby.players[0].client.get(lobby.url("/lobby")).json()["floor_count"] == 5


def test_host_cannot_change_floor_count_or_order_after_the_game_starts(lobby_for, db):  # 35, 36
    lobby = started(lobby_for, floors=4)
    count, order = game_row(db, lobby).floor_count, order_of(db, lobby)
    base = f"/api/games/{lobby.code}"
    attempts = [
        ("patch", base, {"floor_count": 8}), ("put", base, {"floor_count": 8}), ("post", base, {"floor_count": 8}),
        ("post", base + "/floors", {"floor_count": 8}), ("post", base + "/start", {"floor_count": 8, "floor_order": [1, 2, 3, 4]}),
        ("post", base + "/team-formation/reopen", {"floor_count": 8}), ("patch", base + "/play/host", {"floor_order": [4, 3, 2, 1]}),
    ]
    for method, url, body in attempts:
        response = getattr(lobby.host, method)(url, json=body)
        assert response.status_code in (404, 405, 409, 422), (method, url, response.status_code)
    again = game_row(db, lobby)
    assert again.floor_count == count and list(again.floor_order) == order
    assert lobby.host.post("/api/games", json={"floor_count": 8}).json()["game_code"] != lobby.code  # only ever a NEW game
    assert game_row(db, lobby).floor_count == count


def test_old_games_are_migrated_to_8_floors_in_the_original_order(tmp_path):
    from alembic import command
    from sqlalchemy import create_engine
    from test_migrations import alembic_config

    url = f"sqlite:///{tmp_path / 'old.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0003")
    engine = create_engine(url)
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO games (id, game_code, status, host_token_hash, host_session_expires_at, min_teams, max_teams,"
            " min_players_per_team, max_players_per_team, base_duration_seconds, team_count, created_at, paused_seconds_total)"
            " VALUES ('g1','ABC234','LOBBY','h','2026-01-01 00:00:00',3,7,3,5,3600,7,'2026-01-01 00:00:00',0)"))
    command.upgrade(cfg, "head")
    with engine.connect() as c:
        row = c.execute(text("SELECT floor_count, floor_order, host_plays FROM games")).one()
        order = json.loads(row.floor_order) if isinstance(row.floor_order, str) else row.floor_order
        assert row.floor_count == 8 and order == list(range(1, 9)) and not row.host_plays
        assert c.execute(text("SELECT count(*) FROM players WHERE is_host_player")).scalar() == 0
    command.downgrade(cfg, "0003")
    command.upgrade(cfg, "head")
    engine.dispose()


# ============================================================ playing N floors (11, 12, 20-24)
@pytest.mark.parametrize("floors", [3, 5, 8])
def test_a_game_progresses_through_exactly_n_floors_and_the_final_door_opens(lobby_for, db, floors):
    lobby = started(lobby_for, floors=floors)  # 11, 12, 20, 21, 22, 23
    order = order_of(db, lobby)
    team = team_players(lobby, 1, per_team=1)
    me = team[0]
    assert me.call("POST", "/final-door", {"code": "AAA"}).status_code == 409  # locked: nothing collected
    assert error_code(me.call("POST", "/final-door", {"code": "AAA"})) == "FINAL_DOOR_LOCKED"
    assert error_code(me.call("POST", "/floor/next")) == "FLOOR_NOT_COMPLETE"  # no skipping

    seen = []
    for _ in range(floors):
        state = me.state()
        seen.append(state["room"]["floor"])
        assert state["team"]["floor"] == state["room"]["floor"] == state["progress"]["floor"]
        assert state["game"]["total_floors"] == floors == state["progress"]["total_floors"]
        assert [f["floor"] for f in state["fragments"]] == list(range(1, floors + 1))
        assert state["final_door"]["length"] == floors
        solve_floor(team, LIBRARY, climb=len(seen) < floors)
    assert seen == list(range(1, floors + 1))  # 24: always Floor 1, 2, 3 ... N

    final = me.state()
    assert final["can_advance"] is False and final["final_door"]["status"] == "AVAILABLE"
    assert final["final_door"]["collected"] == floors and len(final["final_door"]["runes"]) == floors
    assert error_code(me.call("POST", "/floor/next")) == "NO_NEXT_FLOOR"

    rows = db.scalars(select(TeamFloorState).where(TeamFloorState.team_id == lobby.team(1)["team_id"])
                      .order_by(TeamFloorState.floor_no)).all()
    assert [r.floor_no for r in rows] == list(range(1, floors + 1)) and all(r.fragment_awarded for r in rows)
    played = [LIBRARY.presets[r.preset_id]["floor"] for r in rows]
    assert played == order  # the stored hidden order is what was really played
    assert len({LIBRARY.presets[r.preset_id]["fragment"]["id"] for r in rows}) == floors  # one fragment per floor

    played_presets = {i + 1: LIBRARY.presets[r.preset_id] for i, r in enumerate(rows)}
    code = final_code(played_presets)
    assert len(code) == floors
    short = me.call("POST", "/final-door", {"code": code[:-1]})
    assert short.status_code == 200 and short.json()["result"]["result"] == "INVALID"
    assert str(floors) in short.json()["result"]["message"]
    wrong = me.call("POST", "/final-door", {"code": "".join(reversed(code)) if code != code[::-1] else "Z" * floors})
    assert wrong.json()["result"]["result"] == "WRONG"
    opened = me.call("POST", "/final-door", {"code": code})
    assert opened.status_code == 200 and opened.json()["result"]["result"] == "CORRECT"
    game = game_row(db, lobby)
    assert game.status == "FINISHED" and game.winner_team_id == lobby.team(1)["team_id"]


def test_unselected_internal_floors_cannot_be_reached(lobby_for, db):  # 19
    lobby = started(lobby_for, floors=3)
    order = order_of(db, lobby)
    me = team_players(lobby, 1, per_team=1)[0]
    here = {o["id"] for o in me.state()["room"]["objects"]}
    for floor in set(range(1, 9)) - set(order):
        for preset in pr.presets_for_floor(LIBRARY, floor):
            foreign = [o["id"] for o in preset["objects"] if o["id"] not in here]
            for object_id in foreign[:3]:
                assert me.investigate(object_id).status_code == 404
    play_whole_building([me], LIBRARY)
    assert sorted(preset_floors(db, lobby, 1)) == sorted(order)
    assert set(preset_floors(db, lobby, 1)).isdisjoint(set(range(1, 9)) - set(order))


# ============================================================ what players can see (25-27)
FORBIDDEN_KEYS = ("floor_order", "floor_plan", "internal", "preset", "answer", "secret")


def test_player_state_never_reveals_the_hidden_floor_or_order(lobby_for, db, live):  # 25, 26
    lobby = started(lobby_for, floors=5)
    me = team_players(lobby, 1, per_team=1)[0]
    everything = []
    sock = lobby.player_socket(live, lobby.players[0])
    for position in range(1, 6):
        state = me.state()
        blob = json.dumps(state)
        everything.append(blob)
        assert state["room"]["floor"] == position
        assert state["room"]["fragment"]["id"] == f"fragment_{position}"
        assert state["room"]["fragment"]["label"] == f"Floor {position} Key Fragment"
        for f in state["fragments"]:
            if f["collected"]:
                assert f["label"] == f"Floor {f['floor']} Key Fragment"
        assert set(re.findall(r"fragment_(\d+)", blob)) <= {str(position)}
        for preset_id in LIBRARY.presets:  # no room/preset id of ANY floor definition
            assert preset_id not in blob
        for key in FORBIDDEN_KEYS:
            assert f'"{key}' not in blob, key
        solve_floor([me], LIBRARY, climb=position < 5)
    pushed = sock.collect(0.5)  # everything the server pushed to this player's socket
    blob = json.dumps([m for m in pushed if m is not sock.CLOSED]) + "".join(everything)
    pushed_types = {m.get("type") for m in pushed if m is not sock.CLOSED}
    assert "FLOOR_STATE" in pushed_types  # the socket really carried floor state, and it was checked above
    for preset_id in LIBRARY.presets:
        assert preset_id not in blob
    for key in ("floor_order", "floor_plan", "internal_floor"):
        assert key not in blob
    for view in (me.client.get(lobby.url("/lobby")).text, me.client.get("/api/me").text):
        for key in ("floor_order", "floor_plan", "internal_floor", "preset"):
            assert key not in view
    for event in db.scalars(select(GameEvent).where(GameEvent.event_type == "FRAGMENT_AWARDED")).all():
        assert re.fullmatch(r"Floor [1-5] Key Fragment", event.payload["fragment"])
    labels = [json.loads(b)["fragments"] for b in everything][-1]
    assert [f["label"] for f in labels if f["collected"]] == [f"Floor {i} Key Fragment" for i in range(1, 5)]


def test_only_the_host_dashboard_shows_the_internal_plan(lobby_for, db):
    lobby = started(lobby_for, floors=4)
    dash = lobby.host.get(lobby.url("/play/host")).json()
    plan = dash["floor_plan"]
    assert [p["position"] for p in plan] == [1, 2, 3, 4] and [p["internal_floor"] for p in plan] == order_of(db, lobby)
    assert dash["total_floors"] == 4
    me = lobby.players[0]
    assert me.client.get(lobby.url("/play/host")).status_code == 403
    assert "floor_plan" not in me.client.get(lobby.url("/play/state")).text


def test_everyone_in_a_game_shares_the_same_hidden_mapping(lobby_for, db):  # 27, 37
    lobby = started(lobby_for, floors=4, sizes=(2, 2, 1))
    order = order_of(db, lobby)
    teams = {slot: team_players(lobby, slot, per_team=2 if slot < 3 else 1) for slot in (1, 2, 3)}
    teams[3] = [Play(lobby, lobby.players[4])]
    for position in (1, 2):
        titles = set()
        for slot, team in teams.items():
            for member in team:
                state = member.state()
                assert state["room"]["floor"] == position
                titles.add(LIBRARY.presets[next(p for p in LIBRARY.presets if LIBRARY.presets[p]["title"] == state["room"]["title"])]["floor"])
            if position < 2:
                solve_floor(team, LIBRARY, climb=True)
        assert titles == {order[position - 1]}  # every team, every player: same internal floor at this position
    for slot in (1, 2, 3):
        assert preset_floors(db, lobby, slot)[:2] == order[:2]


def test_games_are_independent_of_each_other(lobby_for, second_host, harness, db):  # 37
    first = started(lobby_for, floors=3)
    other_body = second_host.post("/api/games", json={"team_count": 3, "floor_count": 4}).json()
    second = harness(second_host, other_body)
    fill(second, [1, 1, 1]); second.lock(); assert second.host.post(second.url("/start")).status_code == 200
    for lobby, floors in ((first, 3), (second, 4)):
        team = team_players(lobby, 1, per_team=1)
        played = play_whole_building(team, LIBRARY)
        assert sorted(played) == list(range(1, floors + 1))
        assert team[0].call("POST", "/final-door", {"code": final_code(played)}).json()["result"]["result"] == "CORRECT"
        assert game_row(db, lobby).status == "FINISHED"
        assert preset_floors(db, lobby, 1) == order_of(db, lobby)
    assert first.players[0].client.get(second.url("/play/state")).status_code == 403  # no cross-game access


def test_floors_cannot_be_skipped_and_fragments_cannot_be_forged(lobby_for):
    lobby = started(lobby_for, floors=3)
    me = team_players(lobby, 1, per_team=1)[0]
    assert error_code(me.call("POST", "/floor/next")) == "FLOOR_NOT_COMPLETE"
    early = me.vault("ZZZZ")  # the vault is still locked: no fragment can be forged
    assert early.status_code == 409, early.text
    assert not any(f["collected"] for f in me.state()["fragments"])
    assert me.call("POST", "/final-door", {"code": "XYZ"}).status_code == 409
    assert me.call("POST", "/floors/3/enter").status_code in (404, 405)
    assert me.state()["room"]["floor"] == 1


# ============================================================ host plays (28-34)
def own_cookie_client(app, source, name):
    only = TestClient(app)
    for cookie in source.cookies.jar:
        if cookie.name == name:
            only.cookies.set(cookie.name, cookie.value, domain=cookie.domain, path=cookie.path)
    return only


def host_play_lobby(lobby_for, name="Hostess", floors=3, **config):
    lobby = lobby_for(3, floor_count=floors, host_plays=True, **config)
    response = lobby.host.post(lobby.url("/host/play"), json={"display_name": name})
    assert response.status_code == 201, response.text
    me = PlayerHandle(lobby.host, response.json()["player_id"], name)
    lobby.players.append(me)
    lobby.join("Bea"), lobby.join("Cy")
    lobby.start_formation()
    for slot, player in enumerate(lobby.players, start=1):
        assert lobby.assign(slot, player).status_code == 200  # the host approves the leaders, themself included
    return lobby, me


def test_host_only_mode_keeps_todays_behaviour(lobby_for, db):  # 28
    lobby = started(lobby_for, floors=3)
    assert lobby.host.post(lobby.url("/host/play"), json={"display_name": "Sneaky"}).status_code == 409
    assert error_code(lobby.host.post(lobby.url("/host/play"), json={"display_name": "Sneaky"})) == "HOST_PLAY_DISABLED"
    assert db.query(Player).filter_by(is_host_player=True).count() == 0
    me = lobby.host.get("/api/me").json()["host"]
    assert me["host_plays"] is False and me["host_is_playing"] is False
    assert lobby.host.get(lobby.url("/play/host")).status_code == 200  # dashboard works as before
    assert lobby.host.get("/api/host/game").status_code == 200
    assert lobby.lobby()["you"]["role"] == "host"
    assert lobby.host.post(lobby.url("/pause")).json()["game_status"] == "RUNNING"  # full dashboard from pause
    assert lobby.host.post(lobby.url("/resume")).status_code == 200


def test_host_play_must_be_chosen_when_the_game_is_created(lobby_for):
    lobby = lobby_for(3)  # host only
    response = lobby.host.post(lobby.url("/host/play"), json={"display_name": "Hostess"})
    assert response.status_code == 409 and error_code(response) == "HOST_PLAY_DISABLED"


def test_a_playing_host_is_a_normal_player_in_a_team(lobby_for, db):  # 29, 31
    lobby, me = host_play_lobby(lobby_for)
    row = db.get(Player, me.id)
    assert row.is_host_player and row.team_id is not None  # leader of team 1 = a member of it
    again = lobby.host.post(lobby.url("/host/play"), json={"display_name": "Second"})
    assert again.status_code == 409 and error_code(again) == "HOST_ALREADY_PLAYING"
    assert lobby.team(1)["player_count"] == 1 and lobby.team(1)["leader_name"] == "Hostess"
    assert lobby.lock().status_code == 200
    started_response = lobby.host.post(lobby.url("/start"))
    assert started_response.status_code == 200 and started_response.json()["you"]["role"] == "player"
    state = Play(lobby, me).state()
    assert state["room"]["floor"] == 1 and state["game"]["total_floors"] == 3
    assert [m["name"] for m in state["members"]] == ["Hostess"] and state["members"][0]["is_me"]
    assert state["team"]["status"] == "PLAYING"


def test_the_host_joins_a_team_with_a_normal_team_code_and_respects_capacity(lobby_for, db):  # 31
    lobby = lobby_for(3, host_plays=True, min_players_per_team=1, max_players_per_team=2)
    host = lobby.host.post(lobby.url("/host/play"), json={"display_name": "Hostess"})
    me = PlayerHandle(lobby.host, host.json()["player_id"], "Hostess")
    a, b, c, d = lobby.join("Ann"), lobby.join("Bo"), lobby.join("Cy"), lobby.join("Di")
    lobby.start_formation()
    lobby.assign(1, a); lobby.assign(2, b); lobby.assign(3, c)
    code = lobby.team_code(1)
    assert lobby.join_team(d, code).status_code == 200  # team 1 now full (2 of 2)
    full = lobby.host.post(lobby.url("/teams/join"), json={"team_code": code})  # host's PLAYER session, normal rules
    assert full.status_code == 409 and error_code(full) == "TEAM_FULL"
    assert db.get(Player, me.id).team_id is None  # nobody is placed into a team behind their back
    assert lobby.join_team(me, lobby.team_code(2)).status_code == 200
    assert db.get(Player, me.id).team_id == lobby.team(2)["team_id"]
    assert lobby.team(2)["player_count"] == 2


def test_the_host_counts_against_game_capacity(lobby_for):  # 31
    lobby = lobby_for(3, host_plays=True, min_players_per_team=1, max_players_per_team=1)
    lobby.join_many(3)  # capacity 3 x 1 is already used up
    full = lobby.host.post(lobby.url("/host/play"), json={"display_name": "Hostess"})
    assert full.status_code == 409 and error_code(full) == "GAME_FULL"


def test_host_names_follow_the_normal_name_rules(lobby_for):
    lobby = lobby_for(3, host_plays=True)
    lobby.join("Alice")
    assert error_code(lobby.host.post(lobby.url("/host/play"), json={"display_name": " ALICE "})) == "DISPLAY_NAME_TAKEN"
    assert error_code(lobby.host.post(lobby.url("/host/play"), json={"display_name": "<b>"})) == "INVALID_DISPLAY_NAME"
    assert lobby.host.post(lobby.url("/host/play"), json={"display_name": "x", "role": "host"}).status_code == 422


def test_the_playing_host_never_sees_the_dashboard_and_can_play(lobby_for, db, live):  # 29, 30
    lobby, me = host_play_lobby(lobby_for)
    assert lobby.lock().status_code == 200
    stale = lobby.host_socket(live)  # a host socket opened BEFORE the game starts
    stale.wait_for_type("LOBBY_STATE")
    assert lobby.host.post(lobby.url("/start")).status_code == 200
    assert stale.wait_for_type("ERROR")["code"] == "HOST_IS_PLAYING"  # evicted: no dashboard data can follow
    stale.expect_closed()

    assert lobby.host.get(lobby.url("/play/host")).status_code == 403
    assert error_code(lobby.host.get(lobby.url("/play/host"))) == "HOST_IS_PLAYING"
    assert error_code(lobby.host.get("/api/host/game")) == "HOST_IS_PLAYING"
    view = lobby.host.get(lobby.url("/lobby")).json()  # even the lobby read is the PLAYER view now
    assert view["you"]["role"] == "player" and view["host_actions"] is None
    assert all(t["team_code"] is None for t in view["teams"] if t["team_id"] != view["you"]["team_id"])
    assert lobby.host.get("/api/me").json()["host"]["host_is_playing"] is True
    refused = lobby.host_socket(live)
    assert refused.wait_for_type("ERROR")["code"] == "NOT_AUTHORIZED"
    as_player = live(lobby.host, f"/ws/games/{lobby.code}")  # no role asked: the playing host is a player
    assert as_player.wait_for_type("LOBBY_STATE")["payload"]["you"]["role"] == "player"
    assert as_player.wait_for_type("FLOOR_STATE")["payload"]["room"]["floor"] == 1

    pause = lobby.host.post(lobby.url("/pause"))  # host authority is kept, but the reply is NOT a dashboard
    assert pause.status_code == 200 and pause.json() == {"paused": True}
    assert lobby.host.post(lobby.url("/resume")).json() == {"paused": False}

    play = Play(lobby, me)  # and the host really plays
    solve_floor([play], LIBRARY, climb=True)
    assert play.state()["room"]["floor"] == 2
    game = game_row(db, lobby)
    game.status = "FINISHED"
    db.commit()
    assert lobby.host.get(lobby.url("/play/host")).status_code == 200  # the finished game can be reviewed


def test_a_playing_host_gets_no_special_puzzle_information(lobby_for):  # 32
    lobby, me = host_play_lobby(lobby_for)
    lobby.lock(); lobby.host.post(lobby.url("/start"))
    play = Play(lobby, me)
    blob = json.dumps(play.state())
    assert '"answer"' not in blob and "floor_plan" not in blob and "floor_order" not in blob
    other_team = Play(lobby, lobby.players[1]).state()["members"]
    assert [m["name"] for m in play.state()["members"]] == ["Hostess"]  # only their own team
    assert [m["name"] for m in other_team] == ["Bea"]
    assert play.call("POST", "/floor/next").status_code == 409  # normal rules: no skipping
    assert play.call("POST", "/final-door", {"code": "AAA"}).status_code == 409


def test_player_sessions_cannot_use_host_powers(lobby_for):  # 33, 34
    lobby, me = host_play_lobby(lobby_for)
    lobby.lock(); lobby.host.post(lobby.url("/start"))
    player_only = own_cookie_client(lobby.app, lobby.host, "hh_player")  # the playing host's PLAYER session alone
    ordinary = lobby.players[1].client
    team_id = lobby.team(2)["team_id"]
    for client in (player_only, ordinary):
        for method, suffix, body in (
            ("get", "/play/host", None), ("post", "/pause", None), ("post", "/resume", None),
            ("post", "/start", None), ("post", "/team-formation/reopen", None), ("post", "/team-formation/lock", None),
            ("post", f"/players/{lobby.players[1].id}/kick", None),
            ("post", f"/teams/{team_id}/leader", {"player_id": me.id}), ("post", "/host/play", {"display_name": "Zed"}),
        ):
            response = getattr(client, method)(lobby.url(suffix), **({"json": body} if body else {}))
            assert response.status_code == 403 and error_code(response) == "HOST_ONLY", (suffix, response.text)
        assert client.get("/api/host/game").status_code == 401  # needs a host session
    assert player_only.get(lobby.url("/play/state")).status_code == 200  # but it plays like any player


def test_an_ordinary_player_cannot_become_the_host_player(lobby_for, db):  # 34
    lobby = lobby_for(3, host_plays=True)
    alice = lobby.join("Alice")
    assert alice.client.post(lobby.url("/host/play"), json={"display_name": "Mallory"}).status_code == 403
    forged = alice.client.post("/api/games/join", json={"game_code": lobby.code, "display_name": "Mallory", "is_host_player": True})
    assert forged.status_code == 422
    assert db.query(Player).filter_by(is_host_player=True).count() == 0
    assert "is_host_player" not in lobby.host.get(lobby.url("/lobby")).text
    assert alice.client.get(lobby.url("/lobby")).json()["host_plays"] is True  # visible setting, no privilege
    assert alice.client.get(lobby.url("/play/host")).status_code == 403


def test_a_host_only_player_row_is_not_created_by_the_public_join(lobby_for, db):
    lobby = lobby_for(3, host_plays=True)
    lobby.join("Alice")
    assert db.query(Player).filter_by(game_id=lobby.game["game_id"], is_host_player=True).count() == 0
