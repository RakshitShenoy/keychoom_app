"""Content loading and validation.

Game content (room presets, puzzles, quizzes, dares) lives in JSON or YAML files
under ``app/content/<kind>/``. The files are read on the *server only*; answers
are never served to browsers.

Phase 1 provides the loading mechanism plus a validation-hook system. Later phases
register validators (preset solvability, vault-code coverage, hint ladders, ...);
if any validator reports an error the application refuses to start.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

CONTENT_KINDS: tuple[str, ...] = ("presets", "puzzles", "quizzes", "dares")
SUPPORTED_SUFFIXES = {".json", ".yaml", ".yml"}
_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-]+$")


class ContentValidationError(Exception):
    def __init__(self, errors: Iterable[str]) -> None:
        self.errors = list(errors)
        super().__init__("Invalid game content:\n - " + "\n - ".join(self.errors))


@dataclass
class ContentLibrary:
    presets: dict[str, dict[str, Any]] = field(default_factory=dict)
    puzzles: dict[str, dict[str, Any]] = field(default_factory=dict)
    quizzes: dict[str, dict[str, Any]] = field(default_factory=dict)
    dares: dict[str, dict[str, Any]] = field(default_factory=dict)

    def by_kind(self, kind: str) -> dict[str, dict[str, Any]]:
        if kind not in CONTENT_KINDS:
            raise KeyError(kind)
        return getattr(self, kind)

    def is_empty(self) -> bool:
        return not any(self.by_kind(k) for k in CONTENT_KINDS)


# A validator inspects the whole library and returns human-readable error strings.
Validator = Callable[[ContentLibrary], Iterable[str]]
_REGISTERED_VALIDATORS: list[Validator] = []


def register_validator(validator: Validator) -> Validator:
    """Decorator/hook used by later phases to add content checks."""
    _REGISTERED_VALIDATORS.append(validator)
    return validator


def clear_registered_validators() -> None:  # mainly for tests
    _REGISTERED_VALIDATORS.clear()


def _read_file(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    return yaml.safe_load(text)


def load_content(content_dir: Path) -> tuple[ContentLibrary, list[str]]:
    """Read every content file. Returns the library and any structural errors."""
    library = ContentLibrary()
    errors: list[str] = []
    content_dir = Path(content_dir)

    if not content_dir.is_dir():
        return library, [f"Content directory not found: {content_dir}"]

    for kind in CONTENT_KINDS:
        kind_dir = content_dir / kind
        if not kind_dir.is_dir():
            continue  # a missing kind directory simply means "no content yet"
        target = library.by_kind(kind)
        for path in sorted(kind_dir.rglob("*")):
            if (
                not path.is_file()
                or path.suffix.lower() not in SUPPORTED_SUFFIXES
                or path.name.startswith((".", "_"))
            ):
                continue
            label = f"{kind}/{path.relative_to(kind_dir).as_posix()}"
            try:
                data = _read_file(path)
            except (OSError, ValueError, yaml.YAMLError) as exc:
                errors.append(f"{label}: cannot parse file ({exc.__class__.__name__}: {exc})")
                continue
            if not isinstance(data, dict):
                errors.append(f"{label}: top level must be an object/mapping")
                continue
            content_id = data.get("id")
            if not isinstance(content_id, str) or not _ID_PATTERN.match(content_id):
                errors.append(f"{label}: missing or invalid 'id' (letters, digits, _ and - only)")
                continue
            if content_id in target:
                errors.append(f"{label}: duplicate {kind[:-1]} id '{content_id}'")
                continue
            target[content_id] = data
    return library, errors


def validate_content(
    library: ContentLibrary, extra_validators: Iterable[Validator] = ()
) -> list[str]:
    errors: list[str] = []
    for validator in [*_REGISTERED_VALIDATORS, *extra_validators]:
        errors.extend(validator(library))
    return errors


def load_and_validate(
    content_dir: Path, extra_validators: Iterable[Validator] = ()
) -> ContentLibrary:
    """Load + validate; raises ``ContentValidationError`` so startup can be aborted."""
    library, errors = load_content(content_dir)
    errors.extend(validate_content(library, extra_validators))
    if errors:
        raise ContentValidationError(errors)
    return library
