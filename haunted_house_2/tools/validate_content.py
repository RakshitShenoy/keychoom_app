#!/usr/bin/env python3
"""Validate every puzzle, quiz, dare and room preset exactly as the server does at startup, then print a
short report. Exit code 1 when anything is wrong.   python tools/validate_content.py"""
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.content.loader import ContentValidationError, load_and_validate  # noqa: E402
from app.game.presets import validate_building, validate_game_content  # noqa: E402


def main() -> int:
    try:
        lib = load_and_validate(ROOT / "app" / "content", extra_validators=[validate_game_content, validate_building])
    except ContentValidationError as exc:
        print("INVALID CONTENT")
        for err in exc.errors:
            print(" -", err)
        return 1
    per_floor = Counter(p["floor"] for p in lib.presets.values())
    print(f"OK: {len(lib.presets)} presets, {len(lib.puzzles)} puzzles, {len(lib.quizzes)} quiz pools, {len(lib.dares)} dare pools")
    for floor in sorted(per_floor):
        names = ", ".join(sorted(p["title"] for p in lib.presets.values() if p["floor"] == floor))
        print(f"  floor {floor}: {per_floor[floor]} room(s): {names}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
