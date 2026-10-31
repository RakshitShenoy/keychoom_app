"""Jumpscare asset registry. The SERVER chooses which asset a team sees; the browser only displays
the asset it is told about (id, url, duration). Assets are original SVG artwork in ``static/img``."""
from __future__ import annotations

from typing import Any

SCARES: tuple[dict[str, str], ...] = (
    {"id": "eyes", "file": "eyes.svg", "alt": "Pairs of glowing eyes open in the dark"},
    {"id": "ghost", "file": "ghost.svg", "alt": "A screaming ghost lunges at the screen"},
    {"id": "skull", "file": "skull.svg", "alt": "A cracked skull with burning eye sockets"},
    {"id": "grin", "file": "grin.svg", "alt": "A wide, impossible grin"},
    {"id": "hand", "file": "hand.svg", "alt": "A pale hand slams against the glass"},
    {"id": "spider", "file": "spider.svg", "alt": "A giant spider drops from above"},
    {"id": "witch", "file": "witch.svg", "alt": "A hooded witch with a hollow stare"},
    {"id": "clown", "file": "clown.svg", "alt": "A cracked porcelain mask"},
)
SCARE_IDS = tuple(s["id"] for s in SCARES)
_BY_ID = {s["id"]: s for s in SCARES}


def is_known(asset_id: str) -> bool:
    return asset_id in _BY_ID


def asset_public(asset_id: str, display_ms: int) -> dict[str, Any]:
    scare = _BY_ID[asset_id]
    return {
        "asset_id": asset_id,
        "url": f"/static/img/jumpscares/{scare['file']}",
        "alt": scare["alt"],
        "duration_ms": display_ms,
    }
