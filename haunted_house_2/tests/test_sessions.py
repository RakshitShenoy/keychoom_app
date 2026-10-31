from fastapi.testclient import TestClient

from app.db.models import Player
from app.main import create_app
from app.timers.clock import utcnow
from datetime import timedelta


def copy_cookies(source: TestClient, target: TestClient) -> None:
    for cookie in source.cookies.jar:
        target.cookies.set(cookie.name, cookie.value, domain=cookie.domain, path=cookie.path)


def test_no_session_means_empty_identity(client):
    assert client.get("/api/me").json() == {"host": None, "player": None}


def test_host_session_persists_across_requests(client, make_game):
    game = make_game().json()
    for _ in range(3):  # a page refresh is just another request with the same cookie
        me = client.get("/api/me").json()
        assert me["host"]["game_code"] == game["game_code"]
        assert me["host"]["status"] == "LOBBY"
        assert me["player"] is None


def test_player_session_persists_and_identifies_game_and_team(client, make_game):
    game = make_game().json()
    browser = TestClient(client.app)  # a player's own browser: it holds no host cookie
    browser.post("/api/games/join", json={"game_code": game["game_code"], "display_name": "Alice"})
    me = browser.get("/api/me").json()
    assert me["player"]["display_name"] == "Alice"
    assert me["player"]["game_code"] == game["game_code"]
    assert me["player"]["team_id"] is None
    assert me["host"] is None


def test_sessions_survive_a_server_restart(client, make_game, settings):
    game = make_game().json()
    client.post("/api/games/join", json={"game_code": game["game_code"], "display_name": "Alice"})

    restarted = create_app(settings)  # same database file, brand-new process state
    with TestClient(restarted) as client2:
        copy_cookies(client, client2)
        me = client2.get("/api/me").json()
        assert me["host"]["game_code"] == game["game_code"]
        assert me["player"]["display_name"] == "Alice"


def test_tampered_cookie_is_not_accepted(client, make_game, settings):
    make_game()
    assert client.get("/api/me").json()["host"] is not None
    for cookie in client.cookies.jar:  # forge the value of the cookie the browser really holds
        if cookie.name == settings.host_cookie_name:
            cookie.value = "forged-token"
    assert client.get("/api/me").json()["host"] is None


def test_expired_player_session_is_rejected(client, db, make_game):
    game = make_game().json()
    body = client.post(
        "/api/games/join", json={"game_code": game["game_code"], "display_name": "Alice"}
    ).json()
    player = db.get(Player, body["player_id"])
    player.session_expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert client.get("/api/me").json()["player"] is None
    assert client.get("/api/player/lobby").status_code == 401


def test_host_endpoint_requires_host_session(client, make_game):
    assert client.get("/api/host/game").status_code == 401
    make_game()
    response = client.get("/api/host/game")
    assert response.status_code == 200
    assert response.json()["player_count"] == 0


def test_host_sees_joined_players(client, make_game):
    game = make_game().json()
    for name in ("Alice", "Bob"):  # host and player cookies are independent
        client.post("/api/games/join", json={"game_code": game["game_code"], "display_name": name})
    body = client.get("/api/host/game").json()
    assert body["player_count"] == 2
    assert sorted(p["display_name"] for p in body["players"]) == ["Alice", "Bob"]
    assert body["capacity"] == 25 * 10


def test_player_lobby_requires_player_session(client, make_game):
    assert client.get("/api/player/lobby").status_code == 401
    game = make_game().json()
    client.post("/api/games/join", json={"game_code": game["game_code"], "display_name": "Alice"})
    body = client.get("/api/player/lobby").json()
    assert body["you"]["display_name"] == "Alice"
    assert body["game_code"] == game["game_code"]
