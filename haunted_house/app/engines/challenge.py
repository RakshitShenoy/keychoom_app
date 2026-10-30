"""Reusable challenge engine (pure Python: no database, no HTTP).

A *puzzle definition* is a JSON/YAML file under ``content/puzzles/``. It is read on the server only.
The correct answer is NEVER part of :func:`public_view`, so it cannot leak through any payload.

Definition (single step)::

    {"id": "f1_candle", "title": "...", "category": "mathematics", "difficulty": "medium",
     "prompt": "...", "clues": ["optional", "list"], "table": {"columns": [...], "rows": [[...]]},
     "answer_type": "numeric|text|choice|code", "answer": 3, "answers": ["alternative", ...],
     "tolerance": 0, "choices": [{"id": "a", "text": "..."}],
     "wrong_feedback": {"<normalised wrong answer>": "targeted message"},
     "reveal_text": "shown to the team once solved"}

A multi-step puzzle has ``"steps": [<step>, <step>, ...]`` where every step carries its own
``prompt`` / ``answer_type`` / ``answer`` (and optionally ``clues``, ``table``, ``choices``...).
Grading a step returns ``CORRECT`` (puzzle finished), ``PARTIAL`` (this step right, more to go),
``WRONG`` or ``INVALID`` (empty / too long / not a number when a number is required).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

CATEGORIES = (
    "logic", "mathematics", "probability", "statistics", "data_interpretation",
    "numerical_reasoning", "sequences", "patterns", "deduction", "visual_observation", "cryptic",
)
DIFFICULTIES = ("medium", "medium_plus", "hard", "very_hard")
# Intended difficulty ramp of the building (Floors 1-8).
FLOOR_DIFFICULTY = {
    1: "medium", 2: "medium", 3: "medium_plus",
    4: "hard", 5: "hard", 6: "hard", 7: "very_hard", 8: "very_hard",
}
ANSWER_TYPES = ("numeric", "text", "choice", "code")
MAX_SUBMISSION_LENGTH = 64

CORRECT, PARTIAL, WRONG, INVALID = "CORRECT", "PARTIAL", "WRONG", "INVALID"

_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")
_FRACTION_RE = re.compile(r"^\s*([-+]?\d+)\s*/\s*(\d+)\s*$")


@dataclass(frozen=True)
class Grade:
    result: str  # CORRECT | PARTIAL | WRONG | INVALID
    message: str | None = None
    step: int = 0  # index of the step that was graded


def normalize_text(value: str) -> str:
    """Case, accents, punctuation and repeated spaces never matter."""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_code(value: str) -> str:
    """Codes: letters and digits only, upper case (``k7-r4`` == ``K7R4``)."""
    return re.sub(r"[^A-Z0-9]", "", str(value).upper())


def parse_number(value: Any) -> Fraction | None:
    """``"3"``, ``"3.0"``, ``"3 hours"``, ``"1,5"``, ``"3/4"`` -> exact Fraction; otherwise None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return Fraction(str(value))
    text = str(value).strip().replace(",", ".")
    frac = _FRACTION_RE.match(text)
    if frac:
        return Fraction(int(frac.group(1)), int(frac.group(2))) if int(frac.group(2)) else None
    numbers = _NUMBER_RE.findall(text)
    if len(numbers) != 1:
        return None
    return Fraction(numbers[0])


def steps_of(puzzle: dict[str, Any]) -> list[dict[str, Any]]:
    steps = puzzle.get("steps")
    return list(steps) if isinstance(steps, list) and steps else [puzzle]


def step_count(puzzle: dict[str, Any]) -> int:
    return len(steps_of(puzzle))


def _accepted(step: dict[str, Any]) -> list[Any]:
    accepted: list[Any] = []
    if "answer" in step:
        accepted.append(step["answer"])
    accepted.extend(step.get("answers") or [])
    return accepted


def _check(step: dict[str, Any], submission: str) -> bool:
    kind = step.get("answer_type")
    accepted = _accepted(step)
    if kind == "numeric":
        value = parse_number(submission)
        if value is None:
            return False
        tolerance = Fraction(str(step.get("tolerance", 0)))
        return any(
            (target := parse_number(a)) is not None and abs(value - target) <= tolerance
            for a in accepted
        )
    if kind == "choice":
        return normalize_text(submission) in {normalize_text(a) for a in accepted}
    if kind == "code":
        return normalize_code(submission) in {normalize_code(a) for a in accepted}
    return normalize_text(submission) in {normalize_text(a) for a in accepted}  # text


def grade_submission(puzzle: dict[str, Any], step_index: int, submission: Any) -> Grade:
    """Grade ONE submission against the step the team is currently on."""
    steps = steps_of(puzzle)
    if not 0 <= step_index < len(steps):
        return Grade(INVALID, "There is nothing left to answer.", step_index)
    step = steps[step_index]
    if not isinstance(submission, str) or not submission.strip():
        return Grade(INVALID, "Type an answer first.", step_index)
    if len(submission) > MAX_SUBMISSION_LENGTH:
        return Grade(INVALID, "That answer is too long.", step_index)
    if step.get("answer_type") == "numeric" and parse_number(submission) is None:
        return Grade(INVALID, "This answer must be a number.", step_index)

    if _check(step, submission):
        if step_index + 1 < len(steps):
            return Grade(PARTIAL, step.get("success_message") or "Correct. There is more to solve.", step_index)
        return Grade(CORRECT, step.get("success_message") or "Correct!", step_index)

    feedback = step.get("wrong_feedback") or {}
    key = normalize_text(submission)
    return Grade(WRONG, feedback.get(key) or "That is not right. Think it through again.", step_index)


def public_view(puzzle: dict[str, Any], step_index: int = 0) -> dict[str, Any]:
    """Everything a player may see while investigating. Contains no answer fields."""
    steps = steps_of(puzzle)
    step_index = max(0, min(step_index, len(steps) - 1))
    step = steps[step_index]
    view: dict[str, Any] = {
        "puzzle_id": puzzle["id"],
        "title": puzzle.get("title", ""),
        "category": puzzle.get("category"),
        "difficulty": puzzle.get("difficulty"),
        "step": step_index + 1,
        "step_count": len(steps),
        "prompt": step.get("prompt", ""),
        "answer_type": step.get("answer_type"),
        "placeholder": step.get("placeholder", ""),
    }
    if step.get("clues"):
        view["clues"] = [str(c) for c in step["clues"]]
    if step.get("table"):
        view["table"] = {
            "columns": [str(c) for c in step["table"].get("columns", [])],
            "rows": [[str(v) for v in row] for row in step["table"].get("rows", [])],
        }
    if step.get("answer_type") == "choice":
        view["choices"] = [{"id": str(c["id"]), "text": str(c["text"])} for c in step.get("choices", [])]
    return view


def validate_puzzle(puzzle: dict[str, Any]) -> list[str]:
    """Structural + self-consistency checks (the stored answer must grade as correct)."""
    pid = puzzle.get("id", "?")
    errors: list[str] = []
    if puzzle.get("category") not in CATEGORIES:
        errors.append(f"puzzle {pid}: category must be one of {', '.join(CATEGORIES)}")
    if puzzle.get("difficulty") not in DIFFICULTIES:
        errors.append(f"puzzle {pid}: difficulty must be one of {', '.join(DIFFICULTIES)}")
    if not isinstance(puzzle.get("title"), str) or not puzzle["title"].strip():
        errors.append(f"puzzle {pid}: missing title")
    if not isinstance(puzzle.get("reveal_text"), str) or not puzzle["reveal_text"].strip():
        errors.append(f"puzzle {pid}: missing reveal_text (what the team learns when it is solved)")

    steps = steps_of(puzzle)
    for i, step in enumerate(steps):
        label = f"puzzle {pid} step {i + 1}"
        if not isinstance(step.get("prompt"), str) or not step["prompt"].strip():
            errors.append(f"{label}: missing prompt")
        if step.get("answer_type") not in ANSWER_TYPES:
            errors.append(f"{label}: answer_type must be one of {', '.join(ANSWER_TYPES)}")
            continue
        accepted = _accepted(step)
        if not accepted:
            errors.append(f"{label}: missing answer")
            continue
        if step["answer_type"] == "choice":
            ids = [str(c.get("id")) for c in step.get("choices", []) if isinstance(c, dict)]
            if len(ids) < 2 or len(set(ids)) != len(ids):
                errors.append(f"{label}: a choice puzzle needs at least 2 choices with unique ids")
            elif not all(str(a) in ids for a in accepted):
                errors.append(f"{label}: the answer is not one of the choice ids")
        for answer in accepted:
            if _check(step, str(answer)) is False:
                errors.append(f"{label}: stored answer {answer!r} does not grade as correct")
    return errors
