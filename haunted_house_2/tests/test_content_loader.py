import json

import pytest

from app.content.loader import (
    ContentValidationError,
    clear_registered_validators,
    load_and_validate,
    load_content,
    register_validator,
)


@pytest.fixture(autouse=True)
def _clean_validators():
    clear_registered_validators()
    yield
    clear_registered_validators()


@pytest.fixture
def content_dir(tmp_path):
    for kind in ("presets", "puzzles", "quizzes", "dares"):
        (tmp_path / kind).mkdir()
    return tmp_path


def test_empty_content_is_valid(content_dir):
    library = load_and_validate(content_dir)
    assert library.is_empty()


def test_loads_json_and_yaml(content_dir):
    (content_dir / "puzzles" / "p1.json").write_text(json.dumps({"id": "p1", "kind": "logic"}))
    (content_dir / "quizzes" / "q1.yaml").write_text("id: q1\nquestions: []\n")
    library = load_and_validate(content_dir)
    assert library.puzzles["p1"]["kind"] == "logic"
    assert "q1" in library.quizzes


def test_missing_directory_reported(tmp_path):
    _library, errors = load_content(tmp_path / "nope")
    assert errors and "not found" in errors[0]


def test_structural_errors_collected(content_dir):
    (content_dir / "puzzles" / "no_id.json").write_text(json.dumps({"kind": "logic"}))
    (content_dir / "puzzles" / "bad.json").write_text("{not json")
    (content_dir / "puzzles" / "list.json").write_text("[1, 2]")
    (content_dir / "dares" / "a.json").write_text(json.dumps({"id": "d1"}))
    (content_dir / "dares" / "b.json").write_text(json.dumps({"id": "d1"}))
    _library, errors = load_content(content_dir)
    assert len(errors) == 4
    with pytest.raises(ContentValidationError):
        load_and_validate(content_dir)


def test_underscore_files_are_ignored(content_dir):
    (content_dir / "presets" / "_template.json").write_text("{broken")
    assert load_and_validate(content_dir).is_empty()


def test_validation_hooks_can_block_startup(content_dir):
    (content_dir / "presets" / "f1a.json").write_text(json.dumps({"id": "f1a"}))

    @register_validator
    def needs_objects(library):
        return [f"preset {pid} has no objects" for pid, p in library.presets.items() if "objects" not in p]

    with pytest.raises(ContentValidationError) as exc_info:
        load_and_validate(content_dir)
    assert "preset f1a has no objects" in str(exc_info.value)


def test_extra_validators_argument(content_dir):
    with pytest.raises(ContentValidationError):
        load_and_validate(content_dir, extra_validators=[lambda _lib: ["always fails"]])


def test_shipped_content_directory_is_valid():
    from app.config import APP_DIR

    library = load_and_validate(APP_DIR / "content")  # raises if any shipped content is invalid
    assert not library.is_empty()
