"""Display-name validation."""
from __future__ import annotations

import unicodedata

# Letters, numbers and combining marks (so Devanagari, Tamil, accents, etc. work),
# plus space, apostrophe, dot, hyphen and underscore. Everything else (symbols,
# emoji, control and zero-width characters, markup characters) is rejected.
_ALLOWED_PUNCTUATION = frozenset(" '.-_")
_ALLOWED_CATEGORY_PREFIXES = ("L", "N", "M")


class InvalidDisplayName(ValueError):
    pass


def _is_allowed(ch: str) -> bool:
    return ch in _ALLOWED_PUNCTUATION or unicodedata.category(ch).startswith(
        _ALLOWED_CATEGORY_PREFIXES
    )


def normalize_display_name(raw: str, min_length: int = 2, max_length: int = 24) -> str:
    if not isinstance(raw, str):
        raise InvalidDisplayName("Display name must be text.")
    text = unicodedata.normalize("NFKC", raw)
    text = " ".join(text.split())  # trim and collapse whitespace
    if len(text) < min_length:
        raise InvalidDisplayName(f"Display name must be at least {min_length} characters.")
    if len(text) > max_length:
        raise InvalidDisplayName(f"Display name must be at most {max_length} characters.")
    if not all(_is_allowed(ch) for ch in text):
        raise InvalidDisplayName(
            "Display name may only contain letters, numbers, spaces and _ - . '"
        )
    if not any(unicodedata.category(ch).startswith(("L", "N")) for ch in text):
        raise InvalidDisplayName("Display name must contain at least one letter or number.")
    return text


def display_name_key(name: str) -> str:
    """Case-insensitive identity used for the per-game uniqueness rule."""
    return name.casefold()
