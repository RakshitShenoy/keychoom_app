def code_of(response):
    return response.json()["error"]["code"]


def test_public_chat_works_before_team_formation(make_lobby):
    lobby = make_lobby()
    alice, bob = lobby.join("Alice"), lobby.join("Bob")
    assert alice.client.post(lobby.url("/chat"), json={"body": "hello everyone"}).status_code == 201
    assert bob.client.post(lobby.url("/chat"), json={"body": "hi Alice"}).status_code == 201

    for view in (lobby.lobby(), bob.client.get(lobby.url("/lobby")).json()):
        assert view["chat"]["enabled"] is True and view["chat"]["closed_message"] is None
        assert [(m["display_name"], m["body"]) for m in view["chat"]["messages"]] == [
            ("Alice", "hello everyone"), ("Bob", "hi Alice"),
        ]


def test_messages_are_stored_with_game_player_and_timestamp(make_lobby, db):
    from sqlalchemy import select

    from app.db.models import ChatMessage

    lobby = make_lobby()
    alice = lobby.join("Alice")
    alice.client.post(lobby.url("/chat"), json={"body": "boo"})
    row = db.scalars(select(ChatMessage)).one()
    assert row.game_id == lobby.game["game_id"] and row.player_id == alice.id
    assert row.display_name == "Alice" and row.created_at is not None


def test_public_chat_is_disabled_once_team_formation_begins(make_lobby):
    lobby = make_lobby()
    alice = lobby.join("Alice")
    alice.client.post(lobby.url("/chat"), json={"body": "before"})
    lobby.start_formation()

    response = alice.client.post(lobby.url("/chat"), json={"body": "after"})
    assert response.status_code == 409 and code_of(response) == "CHAT_CLOSED"

    view = alice.client.get(lobby.url("/lobby")).json()
    assert view["chat"]["enabled"] is False
    assert "closed" in view["chat"]["closed_message"].lower()
    assert [m["body"] for m in view["chat"]["messages"]] == ["before"]  # history stays readable


def test_chat_stays_closed_after_lock_and_reopen(make_lobby):
    lobby = make_lobby()
    lobby.build_full_teams()
    player = lobby.players[0]
    assert lobby.lock().status_code == 200
    assert player.client.post(lobby.url("/chat"), json={"body": "x"}).status_code == 409
    assert lobby.host.post(lobby.url("/team-formation/reopen")).status_code == 200
    assert player.client.post(lobby.url("/chat"), json={"body": "x"}).status_code == 409


def test_invalid_messages_rejected(make_lobby):
    lobby = make_lobby()
    alice = lobby.join("Alice")
    assert alice.client.post(lobby.url("/chat"), json={"body": "   "}).status_code == 422
    assert alice.client.post(lobby.url("/chat"), json={"body": "x" * 301}).status_code == 422
    assert alice.client.post(lobby.url("/chat"), json={"body": "hi", "role": "host"}).status_code == 422


def test_chat_requires_a_player_session_of_that_game(make_lobby, second_host):
    lobby = make_lobby()
    lobby.join("Alice")
    assert lobby.host.post(lobby.url("/chat"), json={"body": "hi"}).status_code == 401  # host has no player

    other = second_host.post("/api/games", json={"team_count": 3}).json()
    outsider = second_host  # a host of another game, not a player of this one
    assert outsider.post(lobby.url("/chat"), json={"body": "hi"}).status_code == 401


def test_chat_is_rate_limited(make_lobby):
    lobby = make_lobby()
    alice = lobby.join("Alice")
    results = [
        alice.client.post(lobby.url("/chat"), json={"body": f"m{i}"}).status_code for i in range(7)
    ]
    assert results[:5] == [201] * 5
    assert results[5:] == [429, 429]
