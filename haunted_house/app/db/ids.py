from __future__ import annotations

import secrets


def new_id() -> str:
    """32-character cryptographically random hex id (not guessable, not sequential)."""
    return secrets.token_hex(16)
