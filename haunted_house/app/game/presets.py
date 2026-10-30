"""Room-preset helpers and the game-content validator (pure Python, no database).

Preset shape (``content/presets/*.json``)::

    {"id": "floor1_cellar", "floor": 1, "title": "...", "intro": "...",
     "fragment": {"id": "fragment_1", "label": "..."},
     "vault": {"label": "...", "length": 4, "code": "K7R4", "requires": ["vent"], "position": {"x": 82, "y": 66}},
     "objects": [{"id": "candle", "type": "puzzle|quiz|dare", "label": "...", "icon": "...",
                  "position": {"x": 14, "y": 62}, "requires": [], "optional": false,
                  "puzzle": "<puzzle id>"                       # type puzzle
                  "quiz": {"pool": "...", "questions": 5, "time_limit_seconds": 120, "pass_score": 3},  # type quiz
                  "dare_pool": "<dare pool id>",                # type dare (always optional)
                  "vault_positions": [0], "reward_text": "..."}],
     "hints": {"<object id or vault>": ["hint 1", "hint 2", "hint 3", "hint 4"]}}

The *vault code* belongs to the preset and is only ever sent to a team one character at a time, after
the object that owns that position has been solved.
"""
from __future__ import annotations

import random
from typing import Any, Iterable, Mapping

from app.content.loader import ContentLibrary
from app.engines import challenge, dare, quiz

OBJECT_TYPES = ("puzzle", "quiz", "dare", "trap")
TOTAL_FLOORS = 8
VAULT_ID = "vault"
HINT_LEVELS = 4
VAULT_LENGTH = 4
_CODE_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")


# ---------------------------------------------------------------- lookups
def presets_for_floor(library: ContentLibrary, floor: int) -> list[dict[str, Any]]:
    return sorted((p for p in library.presets.values() if p.get("floor") == floor), key=lambda p: p["id"])


def objects_by_id(preset: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {o["id"]: o for o in preset.get("objects", [])}


def is_optional(obj: Mapping[str, Any]) -> bool:
    return bool(obj.get("optional")) or obj.get("type") in ("dare", "trap")


def prerequisites_met(obj: Mapping[str, Any], solved: Iterable[str]) -> bool:
    return set(obj.get("requires", [])) <= set(solved)


def vault_unlocked(preset: Mapping[str, Any], solved: Iterable[str]) -> bool:
    return set(preset["vault"].get("requires", [])) <= set(solved)


def current_objective(preset: Mapping[str, Any], solved: Iterable[str]) -> str | None:
    """The object a hint is about: the first unsolved, available, required object; the vault when
    only the vault is left; None when nothing is left to do."""
    solved = set(solved)
    for obj in preset["objects"]:
        if obj["id"] not in solved and not is_optional(obj) and prerequisites_met(obj, solved):
            return obj["id"]
    return VAULT_ID if vault_unlocked(preset, solved) else None


def fragment_id_for(floor: int) -> str:
    return f"fragment_{floor}"


def final_door_code(presets_by_floor: Mapping[int, Mapping[str, Any]]) -> str:
    """The 8-rune code of a team's final door: one rune per floor, from the presets it really played."""
    return "".join(str(presets_by_floor[f]["fragment"]["rune"]) for f in range(1, TOTAL_FLOORS + 1))


def vault_chars_for(preset: Mapping[str, Any], obj: Mapping[str, Any]) -> dict[str, str]:
    """``{"0": "K"}``: position -> character, taken from the preset's secret code."""
    code = preset["vault"]["code"]
    return {str(i): code[i] for i in obj.get("vault_positions", [])}


def reward_text(library: ContentLibrary, obj: Mapping[str, Any]) -> str:
    if obj.get("reward_text"):
        return str(obj["reward_text"])
    puzzle = library.puzzles.get(obj.get("puzzle", ""))
    return str(puzzle["reveal_text"]) if puzzle else ""


def assign_presets(preset_ids: list[str], team_ids: list[str], seed: str) -> dict[str, str]:
    """Shuffle the presets and deal them round-robin: duplicates only when teams outnumber presets."""
    if not preset_ids:
        raise ValueError("No preset available for this floor.")
    rng = random.Random(seed)
    order = sorted(preset_ids)
    rng.shuffle(order)
    return {team_id: order[i % len(order)] for i, team_id in enumerate(sorted(team_ids))}


# ---------------------------------------------------------------- validation
def _closure(requires: Mapping[str, list[str]], start: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    stack = list(start)
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(requires.get(node, []))
    return seen


def _has_cycle(requires: Mapping[str, list[str]]) -> bool:
    state: dict[str, int] = {}

    def visit(node: str) -> bool:
        if state.get(node) == 1:
            return True
        if state.get(node) == 2:
            return False
        state[node] = 1
        if any(visit(dep) for dep in requires.get(node, []) if dep in requires):
            return True
        state[node] = 2
        return False

    return any(visit(n) for n in requires)


def _validate_preset(library: ContentLibrary, preset: Mapping[str, Any]) -> list[str]:
    pid = preset.get("id", "?")
    errors: list[str] = []
    floor = preset.get("floor")
    if not isinstance(floor, int) or not 1 <= floor <= 8:
        errors.append(f"preset {pid}: 'floor' must be an integer from 1 to 8")
        floor = None
    for key in ("title", "intro"):
        if not isinstance(preset.get(key), str) or not preset[key].strip():
            errors.append(f"preset {pid}: missing {key}")
    fragment = preset.get("fragment")
    if not isinstance(fragment, dict) or not fragment.get("id") or not fragment.get("label"):
        errors.append(f"preset {pid}: 'fragment' needs an id and a label")
    else:
        if floor and fragment.get("id") != fragment_id_for(floor):
            errors.append(f"preset {pid}: the fragment id must be '{fragment_id_for(floor)}' (one unique fragment per floor)")
        rune = fragment.get("rune")
        if not (isinstance(rune, str) and len(rune) == 1 and rune in _CODE_CHARS):
            errors.append(f"preset {pid}: the fragment needs a 'rune' (one upper-case letter or digit)")

    vault = preset.get("vault")
    objects = preset.get("objects")
    if not isinstance(vault, dict):
        return errors + [f"preset {pid}: missing 'vault'"]
    if not isinstance(objects, list) or not objects:
        return errors + [f"preset {pid}: needs a non-empty 'objects' list"]

    code = vault.get("code")
    length = vault.get("length")
    if length != VAULT_LENGTH:
        errors.append(f"preset {pid}: the vault code must have exactly {VAULT_LENGTH} characters")
    if not isinstance(code, str) or len(code) != VAULT_LENGTH or not set(code) <= _CODE_CHARS:
        errors.append(f"preset {pid}: vault code must be {VAULT_LENGTH} upper-case letters or digits")
        code = None

    ids: list[str] = []
    for o in objects:
        if not isinstance(o, dict) or not isinstance(o.get("id"), str):
            errors.append(f"preset {pid}: an object has no id")
            continue
        ids.append(o["id"])
    if len(set(ids)) != len(ids):
        errors.append(f"preset {pid}: duplicate object ids")
    if VAULT_ID in ids:
        errors.append(f"preset {pid}: 'vault' is reserved and cannot be an object id")
    known = set(ids)

    requires: dict[str, list[str]] = {}
    claimed: dict[int, str] = {}
    for o in objects:
        if not isinstance(o, dict) or o.get("id") not in known:
            continue
        oid = o["id"]
        label = f"preset {pid}, object {oid}"
        if o.get("type") not in OBJECT_TYPES:
            errors.append(f"{label}: type must be one of {', '.join(OBJECT_TYPES)}")
            continue
        deps = o.get("requires", [])
        if not isinstance(deps, list) or not all(d in known and d != oid for d in deps):
            errors.append(f"{label}: 'requires' must list other object ids of this preset")
            deps = []
        requires[oid] = list(deps)
        pos = o.get("position")
        if not (isinstance(pos, dict) and all(isinstance(pos.get(k), (int, float)) and 0 <= pos[k] <= 100 for k in ("x", "y"))):
            errors.append(f"{label}: position needs x and y between 0 and 100")
        if not isinstance(o.get("label"), str) or not o["label"].strip():
            errors.append(f"{label}: missing label")

        kind = o["type"]
        if kind == "puzzle":
            puzzle = library.puzzles.get(o.get("puzzle"))
            if puzzle is None:
                errors.append(f"{label}: unknown puzzle {o.get('puzzle')!r}")
            elif floor and puzzle.get("difficulty") != challenge.FLOOR_DIFFICULTY[floor]:
                errors.append(
                    f"{label}: puzzle {puzzle['id']} is '{puzzle.get('difficulty')}' but floor {floor} "
                    f"needs '{challenge.FLOOR_DIFFICULTY[floor]}'"
                )
        elif kind == "quiz":
            spec = o.get("quiz")
            pool = library.quizzes.get(spec.get("pool")) if isinstance(spec, dict) else None
            if pool is None:
                errors.append(f"{label}: unknown quiz pool")
            else:
                count = spec.get("questions", quiz.DEFAULT_QUESTIONS)
                pass_score = spec.get("pass_score", quiz.DEFAULT_PASS_SCORE)
                limit = spec.get("time_limit_seconds", quiz.DEFAULT_TIME_LIMIT_SECONDS)
                if not (isinstance(count, int) and 1 <= count <= len(pool.get("questions", []))):
                    errors.append(f"{label}: quiz 'questions' must be between 1 and the pool size")
                elif not (isinstance(pass_score, int) and 1 <= pass_score <= count):
                    errors.append(f"{label}: quiz 'pass_score' must be between 1 and 'questions'")
                if not (isinstance(limit, int) and 10 <= limit <= 900):
                    errors.append(f"{label}: quiz 'time_limit_seconds' must be between 10 and 900")
        elif kind == "trap":
            if o.get("vault_positions"):
                errors.append(f"{label}: a trap must not own vault characters")
            if deps:
                errors.append(f"{label}: a trap cannot require other objects")
            if o.get("scare") is not None and not isinstance(o.get("scare"), str):
                errors.append(f"{label}: 'scare' must be an asset id")
        elif kind == "dare":
            if library.dares.get(o.get("dare_pool")) is None:
                errors.append(f"{label}: unknown dare pool {o.get('dare_pool')!r}")
            if o.get("vault_positions"):
                errors.append(f"{label}: dares are optional and must not own vault characters")

        if not (isinstance(o.get("reward_text"), str) and o["reward_text"].strip()) and not (
            kind == "puzzle" and library.puzzles.get(o.get("puzzle"), {}).get("reveal_text")
        ):
            errors.append(f"{label}: needs a reward_text (or a puzzle with reveal_text)")

        positions = o.get("vault_positions", [])
        if not isinstance(positions, list) or not all(isinstance(p, int) and 0 <= p < VAULT_LENGTH for p in positions):
            errors.append(f"{label}: vault_positions must be indexes 0-{VAULT_LENGTH - 1}")
            positions = []
        for p in positions:
            if p in claimed:
                errors.append(f"{label}: vault position {p} is already owned by {claimed[p]}")
            claimed[p] = oid

    if _has_cycle(requires):
        errors.append(f"preset {pid}: the dependency graph has a cycle")

    vault_requires = vault.get("requires")
    if not isinstance(vault_requires, list) or not all(r in known for r in vault_requires):
        errors.append(f"preset {pid}: vault 'requires' must list object ids of this preset")
        vault_requires = []
    needed = _closure(requires, vault_requires)
    for o in objects:
        if isinstance(o, dict) and o.get("id") in known and not is_optional(o) and o["id"] not in needed:
            errors.append(f"preset {pid}: object {o['id']} is not needed to open the vault (unreachable)")
    for o in objects:
        if isinstance(o, dict) and o.get("id") in known and is_optional(o) and o["id"] in needed:
            errors.append(f"preset {pid}: optional object {o['id']} must not be required by the vault")
    if set(claimed) != set(range(VAULT_LENGTH)):
        missing = sorted(set(range(VAULT_LENGTH)) - set(claimed))
        errors.append(f"preset {pid}: vault positions {missing} can never be revealed")
    for pos, oid in claimed.items():
        obj = next((o for o in objects if isinstance(o, dict) and o.get("id") == oid), {})
        if is_optional(obj):
            errors.append(f"preset {pid}: optional object {oid} owns a vault position")

    hints = preset.get("hints")
    trap_ids = {o["id"] for o in objects if isinstance(o, dict) and o.get("type") == "trap"}
    for key in [*[i for i in ids if i not in trap_ids], VAULT_ID]:
        ladder = hints.get(key) if isinstance(hints, dict) else None
        if not (isinstance(ladder, list) and len(ladder) == HINT_LEVELS
                and all(isinstance(h, str) and h.strip() for h in ladder)):
            errors.append(f"preset {pid}: '{key}' needs a hint ladder of exactly {HINT_LEVELS} non-empty hints")
    return errors


def _validate_fragments(library: ContentLibrary) -> list[str]:
    """Cross-preset rules: every preset of a floor yields the SAME unique fragment/rune for that floor,
    and no two floors share a rune (so the final door's 8-rune code is unambiguous)."""
    errors: list[str] = []
    rune_of_floor: dict[int, set[str]] = {}
    for preset in library.presets.values():
        floor, frag = preset.get("floor"), preset.get("fragment")
        if isinstance(floor, int) and isinstance(frag, dict) and isinstance(frag.get("rune"), str):
            rune_of_floor.setdefault(floor, set()).add(frag["rune"])
    for floor, runes in rune_of_floor.items():
        if len(runes) > 1:
            errors.append(f"floor {floor}: its presets must all use the same fragment rune (found {sorted(runes)})")
    singles = [next(iter(r)) for r in rune_of_floor.values() if len(r) == 1]
    if len(set(singles)) != len(singles):
        errors.append("two floors use the same fragment rune")
    return errors


def validate_building(library: ContentLibrary) -> list[str]:
    """The finished game needs every one of the 8 floors (used at startup and by tools/validate_content.py)."""
    errors = []
    for floor in range(1, TOTAL_FLOORS + 1):
        if not presets_for_floor(library, floor):
            errors.append(f"floor {floor} has no room preset")
    return errors


def validate_game_content(library: ContentLibrary) -> list[str]:
    """Registered with the loader: an invalid library stops the application from starting."""
    errors: list[str] = _validate_fragments(library)
    for puzzle in library.puzzles.values():
        errors.extend(challenge.validate_puzzle(puzzle))
    for pool_id, pool in library.quizzes.items():
        errors.extend(quiz.validate_pool(pool_id, pool))
    for pool_id, pool in library.dares.items():
        errors.extend(dare.validate_pool(pool_id, pool))
    for preset in library.presets.values():
        errors.extend(_validate_preset(library, preset))
    return errors
