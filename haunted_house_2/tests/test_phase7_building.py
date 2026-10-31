"""Phase 7: the complete 8-floor game - content, floor progression, fragments, the final door, the winner."""
import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.db.models import Game, Team, TeamFloorState
from app.engines import challenge
from app.game import service
from app.game.presets import TOTAL_FLOORS, validate_building, validate_game_content
from play_support import (
    CONTENT_DIR, climb_to_floor, content_variant, error_code, final_code, load_library, make_settings,
    play_whole_building, reload, solve_floor, start_running, team_players,
)

LIBRARY = load_library()


@pytest.fixture
def settings(tmp_path, request):
    variant = getattr(request, "param", None)  # indirect parametrisation: which room of each floor to offer
    extra = {} if variant is None else {"content_dir": content_variant(tmp_path, LIBRARY, variant)}
    return make_settings(tmp_path, **extra)


# ================================================================ the content itself
def test_the_building_has_eight_floors_with_the_planned_difficulty_ramp():
    assert validate_building(LIBRARY) == [] and validate_game_content(LIBRARY) == []
    by_floor = {f: [p for p in LIBRARY.presets.values() if p["floor"] == f] for f in range(1, 9)}
    assert all(by_floor[f] for f in range(1, 9))
    assert all(len(by_floor[f]) == 2 for f in range(2, 9))  # two tested rooms per floor (Floor 1 keeps its original room)
    for floor, presets in by_floor.items():
        for preset in presets:
            for obj in preset["objects"]:
                if obj["type"] == "puzzle":
                    assert LIBRARY.puzzles[obj["puzzle"]]["difficulty"] == challenge.FLOOR_DIFFICULTY[floor]
    assert [challenge.FLOOR_DIFFICULTY[f] for f in range(1, 9)] == [
        "medium", "medium", "medium_plus", "hard", "hard", "hard", "very_hard", "very_hard"]


def test_every_floor_has_one_unique_fragment_and_a_distinct_rune():
    runes = {}
    for preset in LIBRARY.presets.values():
        assert preset["fragment"]["id"] == f"fragment_{preset['floor']}"
        runes.setdefault(preset["floor"], set()).add(preset["fragment"]["rune"])
    assert all(len(r) == 1 for r in runes.values())
    assert len({next(iter(r)) for r in runes.values()}) == TOTAL_FLOORS


def test_every_room_is_interconnected_with_parallel_clues_and_a_trap():
    for preset in LIBRARY.presets.values():
        if preset["floor"] == 1:
            continue
        kinds = [o["type"] for o in preset["objects"]]
        assert "trap" in kinds and sum(k in ("puzzle", "quiz") for k in kinds) == 3
        parallel = [o for o in preset["objects"] if o["type"] in ("puzzle", "quiz") and not o["requires"]]
        joined = [o for o in preset["objects"] if len(o.get("requires", [])) == 2]
        assert len(parallel) == 2 and len(joined) == 1  # two teammates can work at once; the third needs both


def test_every_puzzle_grades_its_own_stored_answer_and_never_leaks_it():
    for puzzle in LIBRARY.puzzles.values():
        steps = challenge.steps_of(puzzle)
        for i, step in enumerate(steps):
            assert challenge.grade_submission(puzzle, i, str(step["answer"])).result in (challenge.CORRECT, challenge.PARTIAL)
            view = challenge.public_view(puzzle, i)
            assert not {"answer", "answers", "wrong_feedback", "reveal_text", "success_message"} & set(view)
            assert puzzle["reveal_text"] not in json.dumps(view)  # the clue is only given once the puzzle is solved


def test_the_validator_rejects_broken_buildings_and_fragments():
    def broken(mutate, fn=validate_game_content):
        lib = copy.deepcopy(LIBRARY)
        mutate(lib)
        return fn(lib)

    assert broken(lambda l: l.presets.pop("floor5_ballroom") and l.presets.pop("floor5_gallery"), validate_building)
    assert broken(lambda l: l.presets["floor2_kitchen"]["fragment"].update(id="fragment_7"))  # duplicate/forged fragment id
    assert broken(lambda l: l.presets["floor2_kitchen"]["fragment"].update(rune="W"))  # rune shared with floor 1
    assert broken(lambda l: l.presets["floor2_pantry"]["fragment"].pop("rune"))
    trap = lambda l: next(o for o in l.presets["floor2_kitchen"]["objects"] if o["type"] == "trap")
    assert broken(lambda l: trap(l).update(vault_positions=[0]))  # a trap can never own part of the code
    assert broken(lambda l: trap(l).update(requires=["a_cauldron"]))
    assert broken(lambda l: l.presets["floor3_library"]["objects"][2].update(requires=["a_atlas"]))  # joined object loses a clue source -> mismatch
    assert broken(lambda l: l.presets["floor4_nursery"]["vault"].update(requires=[]))  # objects unreachable


# ================================================================ playing the entire building
@pytest.mark.parametrize("settings", [0, 1], indirect=True)
def test_a_team_can_complete_all_eight_floors_and_open_the_final_door(make_lobby, settings, db):
    """Variant 0 and 1 together play EVERY shipped room through the real API (quizzes included)."""
    lobby = start_running(make_lobby)
    a, b, c = team_players(lobby, 1)
    played = play_whole_building([a, b, c], load_library(settings.content_dir))
    assert {p["id"] for p in played.values()} <= {p["id"] for p in load_library(settings.content_dir).presets.values()}
    assert sorted(played) == list(range(1, 9))
    state = a.state()
    assert state["room"]["floor"] == 8 and state["room"]["floor_status"] == "COMPLETED"
    assert [f["collected"] for f in state["fragments"]] == [True] * 8
    assert state["final_door"]["status"] == "AVAILABLE" and state["final_door"]["runes"] == list("WITCHERY")

    response = b.call("POST", "/final-door", {"code": final_code(played)})
    assert response.status_code == 200 and response.json()["result"]["result"] == "CORRECT"
    game = reload(db, Game, lobby.game["game_id"])
    team = reload(db, Team, lobby.team(1)["team_id"])
    assert game.status == "FINISHED" and game.winner_team_id == team.id and team.status == "ESCAPED"
    assert db.query(TeamFloorState).filter_by(team_id=team.id, fragment_awarded=True).count() == 8


# ================================================================ progression is enforced by the server
def test_floors_cannot_be_skipped_or_entered_without_finishing(make_lobby, db):
    lobby = start_running(make_lobby)
    a, b, _ = team_players(lobby, 1)
    assert error_code(a.call("POST", "/floor/next")) == "FLOOR_NOT_COMPLETE"
    a.solve("candle"); a.solve("painting")
    assert error_code(a.call("POST", "/floor/next")) == "FLOOR_NOT_COMPLETE"  # vault not opened yet
    assert error_code(a.vault("K7R4")) == "VAULT_LOCKED"  # the vent is still unsolved
    assert reload(db, Team, lobby.team(1)["team_id"]).current_floor == 1


def test_the_next_floor_is_dealt_by_the_server_and_progress_is_saved(make_lobby, db):
    lobby = start_running(make_lobby)
    players = team_players(lobby, 1)
    played = solve_floor(players, LIBRARY, climb=True)
    state = players[2].state()
    assert played["floor"] == 1 and state["room"]["floor"] == 2 and state["room"]["floor_status"] == "ACTIVE"
    assert state["team"]["floors_completed"] == 1 and state["progress"]["floor"] == 2
    assert state["fragments"][0]["collected"] and not state["fragments"][1]["collected"]
    assert state["room"]["vault"]["revealed"] == [None] * 4 and state["room"]["clues"] == []
    # climbing twice does not create floor 3
    assert error_code(players[0].call("POST", "/floor/next")) == "FLOOR_NOT_COMPLETE"
    assert db.query(TeamFloorState).filter_by(team_id=lobby.team(1)["team_id"]).count() == 2


def test_teams_progress_independently_on_different_floors(make_lobby, db):
    lobby = start_running(make_lobby)
    t1, t2, t3 = team_players(lobby, 1), team_players(lobby, 2), team_players(lobby, 3)
    climb_to_floor(t1, LIBRARY, 3)
    climb_to_floor(t2, LIBRARY, 2)
    assert [t[0].state()["room"]["floor"] for t in (t1, t2, t3)] == [3, 2, 1]
    assert [t[0].state()["team"]["floors_completed"] for t in (t1, t2, t3)] == [2, 1, 0]
    # nothing a team does touches another team's rows
    for slot, expected in ((1, 2), (2, 1), (3, 0)):
        assert db.query(TeamFloorState).filter_by(team_id=lobby.team(slot)["team_id"], fragment_awarded=True).count() == expected


def test_the_final_door_needs_all_eight_fragments(make_lobby):
    lobby = start_running(make_lobby)
    players = team_players(lobby, 1)
    assert error_code(players[0].call("POST", "/final-door", {"code": "WITCHERY"})) == "FINAL_DOOR_LOCKED"
    climb_to_floor(players, LIBRARY, 3)
    assert error_code(players[0].call("POST", "/final-door", {"code": "WITCHERY"})) == "FINAL_DOOR_LOCKED"
    state = players[0].state()
    assert state["final_door"]["status"] == "LOCKED" and state["final_door"]["runes"][2:] == [None] * 6  # unknown runes stay secret


def test_a_forged_fragment_in_the_database_is_not_enough_only_real_floor_rows_count(make_lobby, db):
    lobby = start_running(make_lobby)
    players = team_players(lobby, 1)
    team = reload(db, Team, lobby.team(1)["team_id"])
    floor = db.query(TeamFloorState).filter_by(team_id=team.id, floor_no=1).one()
    floor.fragment_awarded = True  # a fragment without a completed floor / vault
    db.commit()
    assert error_code(players[0].call("POST", "/final-door", {"code": "WITCHERY"})) == "FINAL_DOOR_LOCKED"


def test_the_browser_cannot_send_floors_fragments_or_other_teams(make_lobby):
    lobby = start_running(make_lobby)
    a = team_players(lobby, 1)[0]
    for path, body in (("/floor/next", {"floor": 8}), ("/final-door", {"code": "WITCHERY", "fragments": 8}), ("/vault", {"code": "K7R4", "floor": 8})):
        assert a.call("POST", path, body).status_code == 422


def test_wrong_final_codes_are_refused_and_do_not_end_the_game(make_lobby, db):
    lobby = start_running(make_lobby)
    players = team_players(lobby, 1)
    played = {p["floor"]: p for p in (solve_floor(players, LIBRARY) for _ in range(8))}
    response = players[0].call("POST", "/final-door", {"code": "YRETCHIW"})
    assert response.json()["result"]["result"] == "WRONG"
    assert players[0].call("POST", "/final-door", {"code": "WIT"}).json()["result"]["result"] == "INVALID"
    assert reload(db, Game, lobby.game["game_id"]).status == "RUNNING"
    assert players[0].call("POST", "/final-door", {"code": final_code(played).lower()}).json()["result"]["result"] == "CORRECT"


# ================================================================ first team to escape wins
def test_first_team_wins_and_everybody_is_told(make_lobby, db, live):
    lobby = start_running(make_lobby)
    t1, t2 = team_players(lobby, 1), team_players(lobby, 2)
    host = lobby.host_socket(live)
    host.wait_for_type("LOBBY_STATE")
    other = lobby.player_socket(live, lobby.players[3])  # a member of team 2
    other.wait_for_type("LOBBY_STATE")
    host.collect(0.5); other.collect(0.5)

    played = play_whole_building(t1, LIBRARY)
    host.collect(0.3); other.collect(0.3)
    assert t1[0].call("POST", "/final-door", {"code": final_code(played)}).status_code == 200

    event = host.wait_for_type("GAME_FINISHED")
    assert event["payload"]["winner_team_name"] == lobby.team(1)["name"]
    assert other.wait_for_type("GAME_FINISHED")["payload"]["winner_team_name"] == lobby.team(1)["name"]
    pushed = other.wait_for(lambda m: m["type"] == "FLOOR_STATE" and m["payload"]["game"]["status"] == "FINISHED")["payload"]
    assert pushed["game"]["winner"] == {"team_name": lobby.team(1)["name"], "is_my_team": False}
    assert t1[0].state()["game"]["winner"]["is_my_team"] is True and t1[0].state()["team"]["status"] == "ESCAPED"
    dash = lobby.host.get(lobby.url("/play/host")).json()
    assert dash["winner"]["team_name"] == lobby.team(1)["name"] and dash["game_status"] == "FINISHED"
    # the losing team can no longer act
    assert error_code(t2[0].investigate("candle")) == "GAME_OVER"


def test_two_teams_at_the_final_door_at_once_produce_exactly_one_winner(make_lobby, db):
    lobby = start_running(make_lobby, team_count=3)
    teams = [team_players(lobby, s) for s in (1, 2)]
    plays = [play_whole_building(t, LIBRARY) for t in teams]
    barrier = threading.Barrier(2)

    def go(i):
        barrier.wait()
        return teams[i][0].call("POST", "/final-door", {"code": final_code(plays[i])})

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(go, range(2)))
    assert sorted(r.status_code for r in results) == [200, 409]
    assert [error_code(r) for r in results if r.status_code == 409] == ["GAME_OVER"]
    game = reload(db, Game, lobby.game["game_id"])
    assert game.status == "FINISHED" and game.winner_team_id in {lobby.team(1)["team_id"], lobby.team(2)["team_id"]}
    assert db.query(Team).filter_by(game_id=game.id, status="ESCAPED").count() == 1


# ================================================================ timers and hints across floors
def test_hints_keep_their_floor_and_text_after_climbing(make_lobby, db):
    lobby = start_running(make_lobby)
    players = team_players(lobby, 1)
    first = players[0].hint(1).json()["result"]
    assert first["about"] == "Black Candle" and first["penalty_seconds"] == 30
    solve_floor(players, LIBRARY)
    second = players[1].hint(2).json()
    state = second["state"]
    assert state["hints"]["used"] == 2 and state["hints"]["remaining"] == 2
    revealed = {h["level"]: h for h in state["hints"]["revealed"]}
    assert revealed[1]["about"] == "Black Candle" and revealed[1]["floor"] == 1  # still the FLOOR 1 hint text
    assert revealed[2]["floor"] == 2 and revealed[2]["about"] != "Black Candle"
    team = reload(db, Team, lobby.team(1)["team_id"])
    assert team.hint_penalty_seconds == 90 and state["timer"]["penalty_seconds"] == 90


def test_an_expired_team_cannot_climb_or_open_the_door(make_lobby, db, app):
    from datetime import timedelta
    from app.timers.clock import utcnow

    lobby = start_running(make_lobby)
    players = team_players(lobby, 1)
    solve_floor(players, LIBRARY, climb=False)
    team = reload(db, Team, lobby.team(1)["team_id"])
    team.deadline_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert error_code(players[0].call("POST", "/floor/next")) == "TEAM_ELIMINATED"
    assert reload(db, Team, team.id).current_floor == 1 and reload(db, Team, team.id).status == "ELIMINATED"


def test_when_every_team_runs_out_of_time_the_game_ends_without_a_winner(make_lobby, db, app):
    from datetime import timedelta
    from app.timers.clock import utcnow

    lobby = start_running(make_lobby)
    for t in db.query(Team).filter_by(game_id=lobby.game["game_id"]).all():
        t.deadline_at = utcnow() - timedelta(seconds=1)
    db.commit()
    service.sweep(app.state.db.session_factory, app.state.game)
    game = reload(db, Game, lobby.game["game_id"])
    assert game.status == "FINISHED" and game.winner_team_id is None
    state = team_players(lobby, 1)[0].state()
    assert state["game"]["status"] == "FINISHED" and state["game"]["winner"] is None and state["timer"]["expired"]


# ================================================================ isolation + refresh (server side)
def test_a_reload_restores_the_same_player_and_all_progress_from_the_server(make_lobby, db):
    from starlette.testclient import TestClient

    lobby = start_running(make_lobby)
    players = team_players(lobby, 1)
    solve_floor(players, LIBRARY)
    players[2].hint(1)
    before = players[2].state()

    reloaded = TestClient(lobby.app)  # "close the tab, reopen the page": a new client with only the cookies
    for cookie in players[2].client.cookies.jar:
        reloaded.cookies.set(cookie.name, cookie.value, domain=cookie.domain, path=cookie.path)
    me = reloaded.get("/api/me").json()["player"]
    assert me["player_id"] == lobby.players[2].id and me["team_id"] is not None
    after = reloaded.get(lobby.url("/play/state")).json()["state"]
    for key in ("team", "fragments", "final_door", "hints", "progress", "room"):
        assert after[key] == before[key], key
    assert after["timer"]["deadline_at"] == before["timer"]["deadline_at"]
    assert db.query(Game).count() == 1 and len(lobby.lobby()["players"]) == 9  # nobody new appeared


def test_a_team_never_sees_another_teams_state(make_lobby):
    lobby = start_running(make_lobby)
    t1, t2 = team_players(lobby, 1), team_players(lobby, 2)
    solve_floor(t1, LIBRARY)
    s2 = t2[0].state()
    assert s2["room"]["floor"] == 1 and not any(f["collected"] for f in s2["fragments"])
    assert [m["name"] for m in s2["members"]] == [p.player.name for p in t2]
