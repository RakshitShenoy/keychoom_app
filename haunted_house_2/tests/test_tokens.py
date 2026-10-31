from app.auth.tokens import generate_token, hash_token, tokens_match


def test_tokens_are_random_and_long():
    tokens = {generate_token() for _ in range(100)}
    assert len(tokens) == 100
    assert all(len(t) >= 40 for t in tokens)


def test_hash_is_deterministic_and_not_plaintext():
    token = generate_token()
    digest = hash_token(token, "secret")
    assert digest == hash_token(token, "secret")
    assert digest != token
    assert token not in digest
    assert len(digest) == 64


def test_hash_depends_on_secret_and_token():
    assert hash_token("abc", "s1") != hash_token("abc", "s2")
    assert hash_token("abc", "s1") != hash_token("abd", "s1")


def test_tokens_match():
    token = generate_token()
    stored = hash_token(token, "secret")
    assert tokens_match(token, stored, "secret")
    assert not tokens_match(token + "x", stored, "secret")
    assert not tokens_match(token, stored, "other-secret")
