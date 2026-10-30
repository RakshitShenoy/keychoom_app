"""Quick-quiz rules (pure Python). The server owns the clock, the question draw and the grading.

* The 5 questions are drawn from a pool with a seed derived from (game, team, object, attempt), so
  the draw is deterministic (testable, reproducible) but differs between teams and attempts.
* Questions are sent one at a time and never carry the answer.
* Reward rule (documented, deterministic): the quiz is *passed* with at least ``pass_score`` correct
  answers; a pass grants the object's reward (vault characters / clue). A fail grants nothing and the
  team may try again with a fresh draw.
"""
from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

DEFAULT_QUESTIONS = 5
DEFAULT_TIME_LIMIT_SECONDS = 120
DEFAULT_PASS_SCORE = 3


def attempt_seed(game_id: str, team_id: str, object_id: str, attempt_no: int) -> int:
    digest = hashlib.sha256(f"{game_id}|{team_id}|{object_id}|{attempt_no}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def draw_questions(pool: Sequence[Mapping[str, Any]], count: int, seed: int) -> list[str]:
    """Ids of ``count`` distinct questions, deterministic for a given seed."""
    ids = sorted(str(q["id"]) for q in pool)
    if count > len(ids):
        raise ValueError("The question pool is smaller than the number of questions requested.")
    return random.Random(seed).sample(ids, count)


def question_public(question: Mapping[str, Any], number: int, total: int) -> dict[str, Any]:
    return {
        "question_id": str(question["id"]),
        "number": number,
        "total": total,
        "category": question.get("category"),
        "prompt": str(question["prompt"]),
        "choices": [{"id": str(c["id"]), "text": str(c["text"])} for c in question["choices"]],
    }


def is_correct(question: Mapping[str, Any], choice_id: str) -> bool:
    return str(choice_id).strip().casefold() == str(question["answer"]).strip().casefold()


def score_attempt(questions: Mapping[str, Mapping[str, Any]], answers: Mapping[str, str]) -> int:
    """Number of correct answers. Unknown ids and unanswered questions simply score nothing."""
    return sum(1 for qid, choice in answers.items() if qid in questions and is_correct(questions[qid], choice))


def passed(score: int, pass_score: int) -> bool:
    return score >= pass_score


def ends_at(started_at: datetime, time_limit_seconds: int) -> datetime:
    return started_at + timedelta(seconds=time_limit_seconds)


def is_over(now: datetime, ends: datetime) -> bool:
    return now >= ends


def validate_pool(pool_id: str, pool: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    questions = pool.get("questions")
    if not isinstance(questions, list) or not questions:
        return [f"quiz pool {pool_id}: needs a non-empty 'questions' list"]
    seen: set[str] = set()
    for q in questions:
        qid = q.get("id") if isinstance(q, dict) else None
        if not isinstance(qid, str) or not qid:
            errors.append(f"quiz pool {pool_id}: a question has no id")
            continue
        if qid in seen:
            errors.append(f"quiz pool {pool_id}: duplicate question id {qid}")
        seen.add(qid)
        choices = q.get("choices")
        ids = [str(c.get("id")) for c in choices if isinstance(c, dict)] if isinstance(choices, list) else []
        if len(ids) < 2 or len(set(ids)) != len(ids):
            errors.append(f"quiz pool {pool_id}, {qid}: needs at least 2 choices with unique ids")
        elif str(q.get("answer")) not in ids:
            errors.append(f"quiz pool {pool_id}, {qid}: the answer is not one of the choice ids")
        if not isinstance(q.get("prompt"), str) or not q["prompt"].strip():
            errors.append(f"quiz pool {pool_id}, {qid}: missing prompt")
    return errors
