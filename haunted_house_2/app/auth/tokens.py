"""Session-token primitives.

Tokens are 256-bit random values handed to the browser in an HttpOnly cookie.
The database only ever stores a keyed hash (HMAC-SHA256 with the server secret),
so a leaked database cannot be replayed as valid sessions.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str, secret_key: str) -> str:
    return hmac.new(secret_key.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def tokens_match(token: str, expected_hash: str, secret_key: str) -> bool:
    return hmac.compare_digest(hash_token(token, secret_key), expected_hash)
