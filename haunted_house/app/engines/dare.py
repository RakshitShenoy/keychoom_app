"""Safe, optional, confirmable dares (pure Python).

A dare is a tiny, friendly, general-audience action (say a line, stretch, wave). Content is reviewed
by a human (``safety_reviewed: true``) AND must only use the allow-listed tags below, so a careless
edit cannot add something physical, risky or embarrassing without the validator noticing.
"""
from __future__ import annotations

import hashlib
import random
from typing import Any, Mapping, Sequence

# Allow-list: anything else (e.g. "physical", "food", "outdoors", "contact", "embarrassing") is rejected.
ALLOWED_TAGS = frozenset({"voice", "social", "creative", "movement_light"})
MAX_TEXT_LENGTH = 200


def dare_seed(game_id: str, team_id: str, object_id: str, attempt_no: int) -> int:
    digest = hashlib.sha256(f"dare|{game_id}|{team_id}|{object_id}|{attempt_no}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def pick_dare(dares: Sequence[Mapping[str, Any]], seed: int) -> Mapping[str, Any]:
    if not dares:
        raise ValueError("The dare pool is empty.")
    ordered = sorted(dares, key=lambda d: str(d["id"]))
    return ordered[random.Random(seed).randrange(len(ordered))]


def dare_public(dare: Mapping[str, Any]) -> dict[str, Any]:
    return {"dare_id": str(dare["id"]), "text": str(dare["text"])}


def validate_pool(pool_id: str, pool: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    dares = pool.get("dares")
    if not isinstance(dares, list) or not dares:
        return [f"dare pool {pool_id}: needs a non-empty 'dares' list"]
    seen: set[str] = set()
    for d in dares:
        did = d.get("id") if isinstance(d, dict) else None
        if not isinstance(did, str) or not did:
            errors.append(f"dare pool {pool_id}: a dare has no id")
            continue
        if did in seen:
            errors.append(f"dare pool {pool_id}: duplicate dare id {did}")
        seen.add(did)
        text = d.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_LENGTH:
            errors.append(f"dare {did}: needs a text of at most {MAX_TEXT_LENGTH} characters")
        if d.get("safety_reviewed") is not True:
            errors.append(f"dare {did}: not marked safety_reviewed: true")
        tags = d.get("tags")
        if not isinstance(tags, list) or not tags or not set(map(str, tags)) <= ALLOWED_TAGS:
            errors.append(f"dare {did}: tags must be a non-empty subset of {sorted(ALLOWED_TAGS)}")
    return errors
