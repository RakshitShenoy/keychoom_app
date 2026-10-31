import pytest
from sqlalchemy import select

from app.auth.tokens import hash_token
from app.db.models import Game, GameEvent, Player


def join(client, code, name):
    return client.post("/api/games/join", json={"game_code": code, "display_name": name})


@pytest.fixture
def game(make_game):
    return make_game().json()


def test_player_can_join(client, db, game, settings):
    response = join(client, game["game_code"], "Alice")
    assert response.status_code == 201
    body = response.json()
    assert body["display_name"] == "Alice"
    assert body["team_id"] is None
    assert body["status"] == "LOBBY"
    assert body["session_established"] is True
    assert body["pawn_color"].startswith("#") and len(body["pawn_color"]) == 7

    player = db.get(Player, body["player_id"])
    assert player is not None and player.team_id is None
    token = client.cookies.get(settings.player_cookie_name)
    assert token and player.session_token_hash == hash_token(token, settings.secret_key)
    assert player.session_token_hash != token
    assert "HttpOnly" in response.headers["set-cookie"]

    events = db.scalars(
        select(GameEvent).where(GameEvent.event_type == "PLAYER_JOINED")
    ).all()
    assert len(events) == 1


def test_join_is_forgiving_about_code_format(client, game):
    code = game["game_code"]
    messy = f" {code[:3].lower()}-{code[3:].lower()} "
    assert join(client, messy, "Bob").status_code == 201


def test_join_nonexistent_game(client):
    response = join(client, "ZZZZZZ", "Alice")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "GAME_NOT_FOUND"


@pytest.mark.parametrize("status", ["READY", "CUTSCENE", "RUNNING", "FINISHED"])
def test_cannot_join_after_game_has_started(client, db, game, status):
    row = db.get(Game, game["game_id"])
    row.status = status
    db.commit()
    response = join(client, game["game_code"], "Late")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "GAME_NOT_ACCEPTING_PLAYERS"


def test_can_join_during_team_formation(client, db, game):
    row = db.get(Game, game["game_id"])
    row.status = "TEAM_FORMATION"
    db.commit()
    assert join(client, game["game_code"], "Early").status_code == 201


@pytest.mark.parametrize("name", ["", " ", "A", "x" * 25, "<b>hi</b>", "💀💀", "!!!"])
def test_invalid_display_names_rejected(client, game, name):
    response = join(client, game["game_code"], name)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_DISPLAY_NAME"


def test_unicode_names_accepted(client, game):
    assert join(client, game["game_code"], "प्रिया").status_code == 201


def test_display_name_unique_case_insensitively(client, game):
    assert join(client, game["game_code"], "Alice").status_code == 201
    response = join(client, game["game_code"], "  ALICE ")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DISPLAY_NAME_TAKEN"


def test_same_name_allowed_in_different_games(client, make_game):
    first = make_game().json()
    second = make_game().json()
    assert join(client, first["game_code"], "Alice").status_code == 201
    assert join(client, second["game_code"], "Alice").status_code == 201


def test_capacity_enforced(client, make_game):
    game = make_game(
        min_teams=3, max_teams=3, min_players_per_team=3, max_players_per_team=3
    ).json()  # capacity 9
    for i in range(9):
        assert join(client, game["game_code"], f"Player {i}").status_code == 201
    response = join(client, game["game_code"], "One too many")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "GAME_FULL"


def test_repeated_wrong_codes_are_rate_limited(client, game):
    for _ in range(3):  # join_failure_limit=3 in the test settings
        assert join(client, "ZZZZZZ", "Mallory").status_code == 404
    blocked = join(client, "ZZZZZZ", "Mallory")
    assert blocked.status_code == 429
    # Even a correct code is refused while blocked (stops brute-force guessing).
    assert join(client, game["game_code"], "Mallory").status_code == 429


def test_valid_joins_never_count_as_failures(client, game):
    for i in range(10):
        assert join(client, game["game_code"], f"Friend {i}").status_code == 201
