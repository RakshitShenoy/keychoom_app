"""Phase 5: the reusable challenge engine, the 5-question quick quiz and safe dares."""
import copy
import json
import shutil
from datetime import timedelta
from pathlib import Path

import pytest

from app.content.loader import ContentLibrary, load_and_validate
from app.db.models import DareConfirmation, ObjectState, QuizAttempt
from app.engines import challenge, dare, quiz
from app.game.presets import validate_game_content
from app.timers.clock import utcnow
from play_support import CODE, CONTENT_DIR, error_code, make_settings, quiz_answers, reload, start_running, team_players


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return load_and_validate(CONTENT_DIR, extra_validators=[validate_game_content])


# ================================================================ pure engine tests
def test_content_loads_and_validates(library):
    assert {"f1_candle", "f1_painting", "f1_vent"} <= set(library.puzzles)  # Floor 1 + the Phase 7 floors
    assert "floor1_cellar" in library.presets and "core_reasoning" in library.quizzes
    assert validate_game_content(library) == []


@pytest.mark.parametrize("puzzle_id, good, bad", [
    ("f1_candle", ["3", " 3 hours ", "3.0"], ["2", "4", "33"]),
    ("f1_painting", ["Maid", "the maid!", "  MAID "], ["butler", "baron", ""]),
    ("f1_vent", ["34", "34.0"], ["26", "30", "35"]),
])
def test_answer_validation_normalises_input(library, puzzle_id, good, bad):
    puzzle = library.puzzles[puzzle_id]
    for text in good:
        assert challenge.grade_submission(puzzle, 0, text).result == challenge.CORRECT, text
    for text in bad:
        assert challenge.grade_submission(puzzle, 0, text).result in {challenge.WRONG, challenge.INVALID}, text


def test_wrong_answers_can_carry_targeted_feedback(library):
    grade = challenge.grade_submission(library.puzzles["f1_vent"], 0, "26")
    assert grade.result == challenge.WRONG and "grows" in grade.message


def test_invalid_input_is_not_a_wrong_answer(library):
    candle = library.puzzles["f1_candle"]
    assert challenge.grade_submission(candle, 0, "three").result == challenge.INVALID
    assert challenge.grade_submission(candle, 0, "").result == challenge.INVALID
    assert challenge.grade_submission(candle, 0, "9" * 200).result == challenge.INVALID
    assert challenge.grade_submission(candle, 0, None).result == challenge.INVALID


def test_supported_answer_types_and_multistep():
    base = {"title": "t", "category": "logic", "difficulty": "medium", "reveal_text": "r"}
    code = {"id": "c", **base, "prompt": "p", "answer_type": "code", "answer": "AB12"}
    choice = {"id": "m", **base, "prompt": "p", "answer_type": "choice", "answer": "b",
              "choices": [{"id": "a", "text": "x"}, {"id": "b", "text": "y"}]}
    frac = {"id": "f", **base, "prompt": "p", "answer_type": "numeric", "answer": "1/2"}
    steps = {"id": "s", **base, "steps": [
        {"prompt": "one", "answer_type": "numeric", "answer": 2},
        {"prompt": "two", "answer_type": "text", "answer": "ghost"}]}
    for puzzle in (code, choice, frac, steps):
        assert challenge.validate_puzzle(puzzle) == []
    assert challenge.grade_submission(code, 0, "ab-12").result == challenge.CORRECT
    assert challenge.grade_submission(choice, 0, "B").result == challenge.CORRECT
    assert challenge.grade_submission(choice, 0, "a").result == challenge.WRONG
    assert challenge.grade_submission(frac, 0, "0.5").result == challenge.CORRECT
    assert challenge.grade_submission(steps, 0, "2").result == challenge.PARTIAL  # not simply pass/fail
    assert challenge.grade_submission(steps, 1, "Ghost").result == challenge.CORRECT
    assert challenge.public_view(steps, 1)["prompt"] == "two" and challenge.public_view(steps, 0)["step_count"] == 2


def test_public_view_never_contains_answers(library):
    for puzzle in library.puzzles.values():
        for index, step in enumerate(challenge.steps_of(puzzle)):  # every step of every puzzle
            view = challenge.public_view(puzzle, index)
            assert not {"answer", "answers", "wrong_feedback", "reveal_text"} & set(view)
            if step["answer_type"] == "numeric":
                assert f'"{step["answer"]}"' not in json.dumps(view)


def test_answers_are_not_in_any_served_file():
    static = Path(CONTENT_DIR).parent.parent / "static"
    served = "\n".join(p.read_text(errors="ignore") for p in static.rglob("*") if p.is_file() and p.suffix in {".js", ".css", ".html"})
    templates = "\n".join(p.read_text() for p in (static.parent / "templates").glob("*.html"))
    for secret in (CODE, "f1_candle", "wrong_feedback", "THIRD frame"):
        assert secret not in served + templates


def test_validator_rejects_broken_content(library):
    def broken(mutate):
        lib = copy.deepcopy(library)
        mutate(lib)
        return validate_game_content(lib)

    assert broken(lambda l: l.puzzles["f1_vent"].update(difficulty="hard"))  # wrong for floor 1
    assert broken(lambda l: l.puzzles["f1_vent"].update(answer_type="magic"))
    assert broken(lambda l: l.presets["floor1_cellar"]["hints"].pop("vent"))
    assert broken(lambda l: l.presets["floor1_cellar"]["objects"][2].update(vault_positions=[2]))  # overlap/missing
    assert broken(lambda l: l.presets["floor1_cellar"]["objects"][0].update(requires=["vent"]))  # cycle
    assert broken(lambda l: l.presets["floor1_cellar"]["vault"].update(code="K7"))


def test_quiz_draw_is_deterministic_and_varies_by_attempt(library):
    pool = library.quizzes["core_reasoning"]["questions"]
    seed = quiz.attempt_seed("g", "t", "o", 1)
    first = quiz.draw_questions(pool, 5, seed)
    assert first == quiz.draw_questions(pool, 5, seed) and len(set(first)) == 5
    assert first != quiz.draw_questions(pool, 5, quiz.attempt_seed("g", "t", "o", 2))
    assert first != quiz.draw_questions(pool, 5, quiz.attempt_seed("g", "other-team", "o", 1))


def test_quiz_scoring_and_reward_rule(library):
    pool = library.quizzes["core_reasoning"]["questions"]
    by_id = {q["id"]: q for q in pool}
    ids = quiz.draw_questions(pool, 5, 1)
    right = {qid: by_id[qid]["answer"] for qid in ids}
    assert quiz.score_attempt(by_id, right) == 5
    three = {qid: (by_id[qid]["answer"] if i < 3 else "zz") for i, qid in enumerate(ids)}
    assert quiz.score_attempt(by_id, three) == 3 and quiz.passed(3, 3) and not quiz.passed(2, 3)
    assert quiz.score_attempt(by_id, {"made-up": "a"}) == 0
    assert all("answer" not in quiz.question_public(q, 1, 5) for q in pool)


def test_dares_are_safe_and_deterministic(library):
    pool = library.dares["core_dares"]
    assert dare.validate_pool("core_dares", pool) == []
    unsafe = copy.deepcopy(pool)
    unsafe["dares"][0]["tags"] = ["physical"]
    unsafe["dares"][1]["safety_reviewed"] = False
    assert len(dare.validate_pool("x", unsafe)) == 2
    assert dare.pick_dare(pool["dares"], 7) == dare.pick_dare(pool["dares"], 7)
    assert set(dare.dare_public(pool["dares"][0])) == {"dare_id", "text"}


# ================================================================ integration: quiz + dare objects
@pytest.fixture
def content_dir(tmp_path):
    """Floor 1 variant with a quiz object and an optional dare object (same engines, new preset)."""
    dest = tmp_path / "content"
    shutil.copytree(CONTENT_DIR, dest)
    (dest / "presets" / "floor1_cellar.json").unlink()
    preset = json.loads((CONTENT_DIR / "presets" / "floor1_cellar.json").read_text())
    preset["id"] = "floor1_quiz"
    candle, _painting, vent = preset["objects"]
    vent["requires"] = ["mirror"]
    preset["objects"] = [
        candle,
        {"id": "mirror", "type": "quiz", "label": "Cracked Mirror", "icon": "M", "position": {"x": 40, "y": 30},
         "requires": ["candle"], "vault_positions": [1, 2],
         "quiz": {"pool": "core_reasoning", "questions": 5, "time_limit_seconds": 120, "pass_score": 3},
         "reward_text": "The mirror clears and shows a row of numbers."},
        vent,
        {"id": "imp", "type": "dare", "label": "Grinning Imp", "icon": "I", "position": {"x": 70, "y": 30},
         "requires": [], "dare_pool": "core_dares", "reward_text": "The imp whispers a secret."},
    ]
    ladder = ["h1", "h2", "h3", "h4"]
    preset["hints"] = {k: ladder for k in ("candle", "mirror", "vent", "imp", "vault")}
    (dest / "presets" / "floor1_quiz.json").write_text(json.dumps(preset))
    assert validate_game_content(load_and_validate(dest)) == []
    return dest


@pytest.fixture
def settings(tmp_path, content_dir):
    return make_settings(tmp_path, content_dir=content_dir)


def solve_quiz(play, wrong=False):
    play.investigate("mirror")
    state = play.quiz_start("mirror").json()["state"]
    answers = quiz_answers()
    last = None
    for _ in range(5):
        question = state["my_investigation"]["quiz"]["question"]
        choice = "zz" if False else (("a" if answers[question["question_id"]] != "a" else "b") if wrong else answers[question["question_id"]])
        last = play.quiz_answer("mirror", question["question_id"], choice)
        assert last.status_code == 200, last.text
        state = last.json()["state"]
    return last


def test_quiz_pass_solves_the_object_and_reveals_the_characters(make_lobby, db):
    lobby = start_running(make_lobby)
    a, b, _ = team_players(lobby, 1)
    a.solve("candle")
    a.investigate("mirror")
    started = a.quiz_start("mirror")
    quiz_state = started.json()["state"]["my_investigation"]["quiz"]
    assert quiz_state["state"] == "IN_PROGRESS" and quiz_state["question"]["total"] == 5
    assert "answer" not in json.dumps(quiz_state)  # one question at a time, never with its answer
    attempt = db.query(QuizAttempt).filter_by(object_id="mirror").one()
    assert attempt.ends_at - attempt.started_at == timedelta(seconds=120)  # the SERVER set the clock
    a.release("mirror")
    result = solve_quiz(b)  # a teammate carries on: the attempt belongs to the team
    assert result.json()["result"] == {"result": "FINISHED", "score": 5, "passed": True, "total": 5}
    state = result.json()["state"]
    assert a.obj("mirror", state)["status"] == "SOLVED" and state["room"]["vault"]["revealed"][:3] == ["K", "7", "R"]
    assert reload(db, QuizAttempt, attempt.id).score == 5


def test_quiz_fail_gives_no_reward_and_allows_a_fresh_try(make_lobby, db):
    lobby = start_running(make_lobby)
    a, *_ = team_players(lobby, 1)
    a.solve("candle")
    first_ids = None
    result = solve_quiz(a, wrong=True)
    assert result.json()["result"]["passed"] is False and result.json()["result"]["score"] == 0
    assert a.obj("mirror", result.json()["state"])["status"] != "SOLVED"
    assert result.json()["state"]["room"]["vault"]["revealed"] == ["K", None, None, None]
    attempts = db.query(QuizAttempt).filter_by(object_id="mirror").all()
    first_ids = attempts[0].question_ids
    assert a.quiz_start("mirror").status_code == 200  # a new draw
    attempts = db.query(QuizAttempt).filter_by(object_id="mirror").order_by(QuizAttempt.started_at).all()
    assert len(attempts) == 2 and attempts[1].question_ids != first_ids


def test_quiz_clock_is_server_side(make_lobby, db):
    lobby = start_running(make_lobby)
    a, *_ = team_players(lobby, 1)
    a.solve("candle")
    a.investigate("mirror")
    state = a.quiz_start("mirror").json()["state"]
    question = state["my_investigation"]["quiz"]["question"]
    attempt = db.query(QuizAttempt).filter_by(object_id="mirror").one()
    attempt.ends_at = utcnow() - timedelta(seconds=1)  # time ran out on the server
    db.commit()
    late = a.quiz_answer("mirror", question["question_id"], quiz_answers()[question["question_id"]])
    assert late.json()["result"]["result"] == "TIME_UP" and late.json()["result"]["passed"] is False
    assert reload(db, QuizAttempt, attempt.id).submitted_at is not None


def test_quiz_rejects_out_of_order_and_unknown_answers(make_lobby):
    lobby = start_running(make_lobby)
    a, *_ = team_players(lobby, 1)
    a.solve("candle")
    a.investigate("mirror")
    assert error_code(a.quiz_answer("mirror", "q_percent", "a")) == "QUIZ_NOT_STARTED"
    question = a.quiz_start("mirror").json()["state"]["my_investigation"]["quiz"]["question"]
    assert error_code(a.quiz_answer("mirror", "not-the-current-one", "a")) == "WRONG_QUESTION"
    assert error_code(a.quiz_answer("mirror", question["question_id"], "zz")) == "INVALID_CHOICE"
    assert error_code(a.answer("mirror", "3")) == "WRONG_OBJECT_TYPE"


def test_overdue_quiz_is_finished_by_the_sweeper(make_lobby, app, db):
    from app.game import service

    lobby = start_running(make_lobby)
    a, *_ = team_players(lobby, 1)
    a.solve("candle")
    a.investigate("mirror")
    a.quiz_start("mirror")
    out = service.sweep(app.state.db.session_factory, app.state.game, utcnow() + timedelta(seconds=500))
    assert out["quizzes"] == 1
    assert db.query(QuizAttempt).filter_by(object_id="mirror").one().submitted_at is not None


def test_dare_assignment_confirmation_and_persistence(make_lobby, db):
    lobby = start_running(make_lobby)
    a, b, _ = team_players(lobby, 1)
    assert a.investigate("candle").status_code == 200
    assert error_code(a.investigate("imp")) == "ALREADY_INVESTIGATING"  # one object per player
    a.release("candle")
    state = a.investigate("imp").json()["state"]
    text = state["my_investigation"]["dare"]["text"]
    assert text and "dare_id" in state["my_investigation"]["dare"]
    assert b.state()["my_investigation"] is None  # teammates never receive someone else's dare
    assert error_code(b.dare_confirm("imp")) == "NOT_INVESTIGATING"
    done = a.dare_confirm("imp")
    assert done.json()["result"]["result"] == "CONFIRMED"
    assert a.obj("imp", done.json()["state"])["status"] == "SOLVED"
    assert db.query(DareConfirmation).count() == 1 and db.query(ObjectState).filter_by(object_id="imp", status="SOLVED").count() == 1
    assert error_code(a.investigate("imp")) == "OBJECT_SOLVED"


def test_declining_a_dare_is_always_allowed_and_blocks_nothing(make_lobby, db):
    lobby = start_running(make_lobby)
    a, b, _ = team_players(lobby, 1)
    a.investigate("imp")
    assert a.release("imp").status_code == 200  # "no thanks"
    assert db.query(DareConfirmation).count() == 0
    assert b.investigate("imp").status_code == 200  # free again, fresh assignment
    b.release("imp")
    for oid in ("candle",):
        a.solve(oid)
    solve_quiz(a)
    a.solve("vent")
    assert a.vault(CODE).json()["result"]["result"] == "CORRECT"  # the vault never needed the dare


def test_quiz_and_dare_state_is_per_team(make_lobby):
    lobby = start_running(make_lobby)
    a1, *_ = team_players(lobby, 1)
    a2, *_ = team_players(lobby, 2)
    a1.solve("candle")
    solve_quiz(a1)
    assert a1.obj("mirror")["status"] == "SOLVED"
    assert a2.obj("mirror")["status"] == "LOCKED" and a2.obj("candle")["status"] == "AVAILABLE"
