import pytest

from app.lobby.names import InvalidDisplayName, display_name_key, normalize_display_name


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Alice", "Alice"),
        ("  Bob   the  Brave ", "Bob the Brave"),
        ("Zoë", "Zoë"),
        ("Rahul_99", "Rahul_99"),
        ("O'Neil-Smith Jr.", "O'Neil-Smith Jr."),
        ("प्रिया", "प्रिया"),
    ],
)
def test_valid_names(raw, expected):
    assert normalize_display_name(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "A",
        "x" * 25,
        "<script>alert(1)</script>",
        "name\x00null",
        "💀💀",
        "!!!",
        "a/b",
    ],
)
def test_invalid_names(raw):
    with pytest.raises(InvalidDisplayName):
        normalize_display_name(raw, min_length=2, max_length=24)


def test_non_string_rejected():
    with pytest.raises(InvalidDisplayName):
        normalize_display_name(None)  # type: ignore[arg-type]


def test_name_key_is_case_insensitive():
    assert display_name_key("ALICE") == display_name_key("alice")
