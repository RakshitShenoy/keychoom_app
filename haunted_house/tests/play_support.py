"""Helpers shared by the Phase 4-6 gameplay tests."""
from __future__ import annotations

import json
from pathlib import Path

from app.config import Settings

CONTENT_DIR = Path(__file__).resolve().parent.parent / "app" / "content"
SOLUTIONS = {"candle": "3", "painting": "maid", "vent": "34"}
CODE = "K7R4"


def make_settings(tmp_path, **overrides) -> Settings:
    values = dict(
        _env_file=None, app_env="test", database_url=f"sqlite:///{tmp_path / 'test.db'}",
        secret_key="test-secret-key", join_failure_limit=3,
        answer_cooldown_seconds=0.0,  # tests resubmit immediately; the cooldown has its own test
        sweeper_interval_seconds=3600,  # the background sweeper never interferes: tests call sweep()
    )
    values.update(overrides)
    return Settings(**values)


def error_code(response) -> str:
    return response.json()["error"]["code"]


class Play:
    """One player's browser, speaking the gameplay API."""

    def __init__(self, lobby, player):
        self.lobby, self.player = lobby, player

    @property
    def client(self):
        return self.player.client

    def call(self, method, suffix, body=None):
        url = self.lobby.url("/play" + suffix)
        if body is None:
            return self.client.request(method, url)
        return self.client.request(method, url, json=body)

    def state(self):
        response = self.call("GET", "/state")
        assert response.status_code == 200, response.text
        return response.json()["state"]

    def obj(self, object_id, state=None):
        return next(o for o in (state or self.state())["room"]["objects"] if o["id"] == object_id)

    def investigate(self, object_id): return self.call("POST", f"/objects/{object_id}/investigate")
    def release(self, object_id): return self.call("POST", f"/objects/{object_id}/release")
    def heartbeat(self, object_id): return self.call("POST", f"/objects/{object_id}/heartbeat")
    def answer(self, object_id, text): return self.call("POST", f"/objects/{object_id}/answer", {"answer": text})
    def vault(self, code): return self.call("POST", "/vault", {"code": code})
    def hint(self, level): return self.call("POST", "/hints", {"level": level})
    def quiz_start(self, object_id): return self.call("POST", f"/objects/{object_id}/quiz/start")
    def quiz_answer(self, object_id, question_id, choice):
        return self.call("POST", f"/objects/{object_id}/quiz/answer", {"question_id": question_id, "choice": choice})
    def dare_confirm(self, object_id): return self.call("POST", f"/objects/{object_id}/dare/confirm")

    def solve(self, object_id, answer=None):
        assert self.investigate(object_id).status_code == 200
        response = self.answer(object_id, answer or SOLUTIONS[object_id])
        assert response.status_code == 200 and response.json()["result"]["result"] == "CORRECT", response.text
        return response


def start_running(make_lobby, team_count=3, per_team=3, **extra):
    lobby = make_lobby(team_count=team_count, min_players=3, max_players=5, **extra)
    lobby.build_full_teams(per_team)
    assert lobby.lock().status_code == 200
    response = lobby.host.post(lobby.url("/start"))
    assert response.status_code == 200, response.text
    return lobby


def team_players(lobby, slot, per_team=3):
    return [Play(lobby, p) for p in lobby.players[(slot - 1) * per_team : slot * per_team]]


def reload(db, model, row_id):
    db.expire_all()
    return db.get(model, row_id)


def quiz_answers() -> dict[str, str]:
    pool = json.loads((CONTENT_DIR / "quizzes" / "core_reasoning.json").read_text())
    return {q["id"]: q["answer"] for q in pool["questions"]}


# ---------------------------------------------------------------------------------------------
# Phase 7-9: a bot that climbs the whole building through the REAL gameplay API
# ---------------------------------------------------------------------------------------------
import shutil  # noqa: E402

from app.content.loader import load_and_validate  # noqa: E402
from app.engines import challenge as challenge_engine  # noqa: E402
from app.game.presets import final_door_code, validate_building, validate_game_content  # noqa: E402


def load_library(content_dir=CONTENT_DIR):
    return load_and_validate(content_dir, extra_validators=[validate_game_content, validate_building])


def preset_by_title(library, title):
    return next(p for p in library.presets.values() if p["title"] == title)


def content_variant(tmp_path, library, variant: int):
    """A content directory where every floor offers exactly ONE room: the first (variant 0) or the
    second (variant 1) preset of the floor. Playing both variants covers every shipped preset."""
    dest = tmp_path / f"content_v{variant}"
    shutil.copytree(CONTENT_DIR, dest)
    for floor in range(1, 9):
        rooms = sorted(p["id"] for p in library.presets.values() if p["floor"] == floor)
        keep = rooms[min(variant, len(rooms) - 1)]
        for room in rooms:
            if room != keep:
                (dest / "presets" / f"{room}.json").unlink()
    return dest


def topo_order(preset):
    """Required objects in an order where every prerequisite comes first."""
    done, order = set(), []
    required = [o for o in preset["objects"] if o["type"] in ("puzzle", "quiz")]
    while len(order) < len(required):
        for obj in required:
            if obj["id"] not in done and set(obj.get("requires", [])) <= done:
                done.add(obj["id"])
                order.append(obj)
    return order


def solve_object(player, obj, library):
    assert player.investigate(obj["id"]).status_code == 200, obj["id"]
    if obj["type"] == "puzzle":
        puzzle = library.puzzles[obj["puzzle"]]
        steps = challenge_engine.steps_of(puzzle)
        for index, step in enumerate(steps):
            response = player.answer(obj["id"], str(step["answer"]))
            assert response.status_code == 200, response.text
            expected = "CORRECT" if index == len(steps) - 1 else "PARTIAL"
            assert response.json()["result"]["result"] == expected, (obj["id"], index, response.text)
    else:  # quiz
        pool = {q["id"]: q["answer"] for q in library.quizzes[obj["quiz"]["pool"]]["questions"]}
        assert player.quiz_start(obj["id"]).status_code == 200
        for _ in range(obj["quiz"]["questions"]):
            question = player.state()["my_investigation"]["quiz"]["question"]
            response = player.quiz_answer(obj["id"], question["question_id"], pool[question["question_id"]])
            assert response.status_code == 200, response.text
        assert response.json()["result"]["result"] == "FINISHED" and response.json()["result"]["passed"]


def solve_floor(players, library, climb=True):
    """Solve the team's current room completely (teammates take turns), open the vault and (optionally)
    climb to the next floor. Returns the preset that was played."""
    state = players[0].state()
    preset = preset_by_title(library, state["room"]["title"])
    for i, obj in enumerate(topo_order(preset)):
        solve_object(players[i % len(players)], obj, library)
    response = players[0].vault(preset["vault"]["code"])
    assert response.status_code == 200 and response.json()["result"]["result"] == "CORRECT", response.text
    if climb and preset["floor"] < 8:
        response = players[1 % len(players)].call("POST", "/floor/next")
        assert response.status_code == 200, response.text
    return preset


def climb_to_floor(players, library, target):
    """Play every floor below ``target`` and stand at the start of floor ``target``."""
    played = []
    while players[0].state()["room"]["floor"] < target:
        played.append(solve_floor(players, library))
    return played


def play_whole_building(players, library):
    played = [solve_floor(players, library, climb=True) for _ in range(8)]
    return {p["floor"]: p for p in played}


def final_code(presets_by_floor):
    return final_door_code(presets_by_floor)
