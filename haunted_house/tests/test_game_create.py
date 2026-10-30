import pytest
from sqlalchemy import select

from app.auth.tokens import hash_token
from app.db.models import Game, GameEvent
from app.lobby.codes import is_valid_game_code_format


def test_create_game_with_explicit_config(client, db, settings):
    response = client.post(
        "/api/games",
        json={
            "min_teams": 4,
            "max_teams": 8,
            "min_players_per_team": 3,
            "max_players_per_team": 6,
            "base_duration_seconds": 5400,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "LOBBY"
    assert body["host_session_established"] is True
    assert body["config"] == {
        "min_teams": 4,
        "max_teams": 8,
        "min_players_per_team": 3,
        "max_players_per_team": 6,
        "base_duration_seconds": 5400,
    }
    assert is_valid_game_code_format(body["game_code"], settings.game_code_length)

    # Persisted in the database.
    game = db.get(Game, body["game_id"])
    assert game is not None and game.game_code == body["game_code"]
    events = db.scalars(select(GameEvent).where(GameEvent.game_id == game.id)).all()
    assert [e.event_type for e in events] == ["GAME_CREATED"]


def test_host_cookie_is_httponly_and_token_is_hashed(client, db, settings):
    response = client.post("/api/games", json={})
    assert response.status_code == 201
    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "samesite=lax" in set_cookie.lower()

    token = client.cookies.get(settings.host_cookie_name)
    assert token
    game = db.get(Game, response.json()["game_id"])
    assert game.host_token_hash != token
    assert token not in game.host_token_hash
    assert game.host_token_hash == hash_token(token, settings.secret_key)
    assert "token" not in response.text.lower()  # token never appears in the body


def test_defaults_applied_when_body_is_empty(make_game, settings):
    body = make_game().json()
    assert body["config"] == {
        "min_teams": settings.min_teams_limit,
        "max_teams": settings.max_teams_limit,
        "min_players_per_team": settings.min_players_limit,
        "max_players_per_team": settings.max_players_limit,
        "base_duration_seconds": settings.default_base_duration_seconds,
    }


def test_game_codes_are_unique(make_game):
    codes = {make_game().json()["game_code"] for _ in range(25)}
    assert len(codes) == 25


@pytest.mark.parametrize(
    "overrides,field",
    [
        ({"min_teams": 2}, "min_teams"),
        ({"max_teams": 26}, "max_teams"),
        ({"min_teams": 10, "max_teams": 5}, "max_teams"),
        ({"min_players_per_team": 2}, "min_players_per_team"),
        ({"max_players_per_team": 11}, "max_players_per_team"),
        ({"min_players_per_team": 8, "max_players_per_team": 5}, "max_players_per_team"),
        ({"base_duration_seconds": 10}, "base_duration_seconds"),
        ({"base_duration_seconds": 10**7}, "base_duration_seconds"),
    ],
)
def test_invalid_limits_rejected(make_game, overrides, field):
    response = make_game(**overrides)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "INVALID_GAME_CONFIG"
    assert field in [d["field"] for d in error["details"]]


def test_invalid_config_creates_no_game(client, make_game, db):
    make_game(min_teams=1)
    assert db.scalars(select(Game)).all() == []


@pytest.mark.parametrize(
    "payload",
    [
        {"min_teams": "5"},
        {"min_teams": 5.5},
        {"min_teams": True},
        {"unexpected": 1},
    ],
)
def test_wrong_types_and_unknown_fields_rejected(client, payload):
    assert client.post("/api/games", json=payload).status_code == 422
