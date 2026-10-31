import pytest

from app.lobby.codes import (
    GAME_CODE_ALPHABET,
    GameCodeGenerationError,
    generate_game_code,
    generate_unique_game_code,
    is_valid_game_code_format,
    normalize_game_code,
)


def test_alphabet_has_no_confusing_characters():
    for ch in "0O1IL":
        assert ch not in GAME_CODE_ALPHABET


def test_generated_code_shape():
    for _ in range(200):
        code = generate_game_code(6)
        assert len(code) == 6
        assert set(code) <= set(GAME_CODE_ALPHABET)
        assert is_valid_game_code_format(code, 6)


def test_codes_are_not_sequential_or_repeating():
    codes = {generate_game_code(6) for _ in range(500)}
    assert len(codes) > 495  # collisions are astronomically unlikely


def test_generate_unique_retries_until_free():
    taken = {"AAAAAA"}
    attempts = []

    def is_taken(code):
        attempts.append(code)
        return len(attempts) < 3  # first two attempts collide

    code = generate_unique_game_code(is_taken, length=6)
    assert len(attempts) == 3
    assert code == attempts[-1]
    assert taken == {"AAAAAA"}


def test_generate_unique_gives_up():
    with pytest.raises(GameCodeGenerationError):
        generate_unique_game_code(lambda _code: True, length=6, max_attempts=5)


@pytest.mark.parametrize(
    "raw,expected",
    [("k7m-2qx", "K7M2QX"), (" k7m 2qx ", "K7M2QX"), ("ABC234", "ABC234"), ("", "")],
)
def test_normalize_game_code(raw, expected):
    assert normalize_game_code(raw) == expected


def test_format_validation():
    assert not is_valid_game_code_format("ABC12", 6)
    assert not is_valid_game_code_format("ABC10O", 6)
