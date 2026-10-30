import pytest
from fastapi.testclient import TestClient

import app.db.models  # noqa: F401  (register models on Base.metadata)
from app.config import Settings
from app.db.base import Base
from app.main import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,  # never read a developer's local .env during tests
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        secret_key="test-secret-key",
        join_failure_limit=3,
    )


@pytest.fixture
def app(settings):
    application = create_app(settings)
    Base.metadata.create_all(application.state.db.engine)
    return application


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db(app):
    session = app.state.db.session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def make_game(client):
    def _make(**overrides):
        return client.post("/api/games", json=overrides)

    return _make


# ---------------------------------------------------------------------------------------------
# Phase 2 harness: a host plus any number of independent player browsers (each with its own cookies)
# ---------------------------------------------------------------------------------------------
import re  # noqa: E402

TEAM_CODE_RE = re.compile(r"^TEAM-[A-HJ-KM-NP-Z2-9]{4}$")


class PlayerHandle:
    def __init__(self, client, player_id, name):
        self.client = client
        self.id = player_id
        self.name = name


class LobbyHarness:
    def __init__(self, app, host_client, game_body):
        self.app = app
        self.host = host_client
        self.game = game_body
        self.code = game_body["game_code"]
        self.players: list[PlayerHandle] = []

    # -- urls / simple calls -------------------------------------------------------------
    def url(self, suffix):
        return f"/api/games/{self.code}{suffix}"

    def join(self, name=None):
        name = name or f"Player {len(self.players) + 1}"
        browser = TestClient(self.app)  # its own cookie jar = its own browser
        response = browser.post(
            "/api/games/join", json={"game_code": self.code, "display_name": name}
        )
        assert response.status_code == 201, response.text
        handle = PlayerHandle(browser, response.json()["player_id"], name)
        self.players.append(handle)
        return handle

    def join_many(self, count):
        return [self.join() for _ in range(count)]

    def lobby(self):
        response = self.host.get(self.url("/lobby"))
        assert response.status_code == 200, response.text
        return response.json()

    def team(self, slot_no):
        return next(t for t in self.lobby()["teams"] if t["slot_no"] == slot_no)

    def start_formation(self):
        response = self.host.post(self.url("/team-formation/start"))
        assert response.status_code == 200, response.text
        return response.json()

    def assign(self, slot_no, player):
        team_id = self.team(slot_no)["team_id"]
        return self.host.post(self.url(f"/teams/{team_id}/leader"), json={"player_id": player.id})

    def team_code(self, slot_no):
        return self.team(slot_no)["team_code"]

    def join_team(self, player, code):
        return player.client.post(self.url("/teams/join"), json={"team_code": code})

    def lock(self):
        return self.host.post(self.url("/team-formation/lock"))

    def kick(self, player):
        return self.host.post(self.url(f"/players/{player.id}/kick"))

    # -- live sockets (Phase 3) --------------------------------------------------------------
    def host_socket(self, live, **kwargs):
        return live(self.host, f"/ws/games/{self.code}?role=host", **kwargs)

    def player_socket(self, live, player, **kwargs):
        return live(player.client, f"/ws/games/{self.code}?role=player", **kwargs)

    def build_full_teams(self, per_team=3):
        """Join team_count * per_team players, start formation, assign leaders, fill the teams."""
        teams = self.game["team_count"]
        self.join_many(teams * per_team)
        self.start_formation()
        for slot in range(1, teams + 1):
            block = self.players[(slot - 1) * per_team : slot * per_team]
            assert self.assign(slot, block[0]).status_code == 200
            code = self.team_code(slot)
            for member in block[1:]:
                response = self.join_team(member, code)
                assert response.status_code == 200, response.text


@pytest.fixture
def make_lobby(app, client):
    def _make(team_count=3, min_players=3, max_players=5, **extra):
        response = client.post(
            "/api/games",
            json={
                "team_count": team_count,
                "min_players_per_team": min_players,
                "max_players_per_team": max_players,
                **extra,
            },
        )
        assert response.status_code == 201, response.text
        return LobbyHarness(app, client, response.json())

    return _make


@pytest.fixture
def second_host(app):
    """A second, independent host browser (for cross-game tampering tests)."""
    return TestClient(app)


@pytest.fixture
def copy_cookies():
    def _copy(source, target):
        for cookie in source.cookies.jar:
            target.cookies.set(cookie.name, cookie.value, domain=cookie.domain, path=cookie.path)

    return _copy


@pytest.fixture
def harness(app):
    """Wrap another host browser + its game body into a LobbyHarness."""
    return lambda host_client, game_body: LobbyHarness(app, host_client, game_body)


# ---------------------------------------------------------------------------------------------
# Phase 3: a WebSocket test client that can never hang a test run
# ---------------------------------------------------------------------------------------------
import queue  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402


class LiveSocket:
    """Wraps TestClient.websocket_connect. A background thread pumps messages into a queue, so
    every wait has a timeout and a missing message fails the test instead of blocking forever."""

    CLOSED = object()

    def __init__(self, client, path, headers=None):
        self._cm = client.websocket_connect(path, headers=headers or {})
        self.ws = self._cm.__enter__()
        self.inbox = queue.Queue()
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self):
        while True:
            try:
                self.inbox.put(self.ws.receive_json())
            except Exception:  # closed by either side
                self.inbox.put(self.CLOSED)
                return

    # -- receiving ---------------------------------------------------------------------------
    def next(self, timeout=5.0):
        try:
            return self.inbox.get(timeout=timeout)
        except queue.Empty:
            raise AssertionError(f"no message within {timeout}s") from None

    def wait_for(self, predicate, timeout=5.0, description="matching message"):
        """Return the first message satisfying ``predicate`` (earlier ones are discarded)."""
        deadline = time.monotonic() + timeout
        seen = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"timed out waiting for {description}; saw {seen}")
            try:
                item = self.inbox.get(timeout=remaining)
            except queue.Empty:
                raise AssertionError(f"timed out waiting for {description}; saw {seen}") from None
            if item is self.CLOSED:
                raise AssertionError(f"socket closed while waiting for {description}; saw {seen}")
            if predicate(item):
                return item
            seen.append(item.get("type"))

    def wait_for_type(self, message_type, timeout=5.0):
        return self.wait_for(lambda m: m["type"] == message_type, timeout, message_type)

    def wait_for_state(self, predicate, timeout=5.0):
        """Wait for a LOBBY_STATE whose payload satisfies ``predicate``; returns the payload."""
        message = self.wait_for(
            lambda m: m["type"] == "LOBBY_STATE" and predicate(m["payload"]),
            timeout, "LOBBY_STATE matching the predicate",
        )
        return message["payload"]

    def expect_closed(self, timeout=5.0):
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError("socket was not closed in time")
            try:
                item = self.inbox.get(timeout=remaining)
            except queue.Empty:
                raise AssertionError("socket was not closed in time") from None
            if item is self.CLOSED:
                return

    def collect(self, seconds):
        """Everything that arrives within ``seconds`` (PING excluded)."""
        end = time.monotonic() + seconds
        items = []
        while time.monotonic() < end:
            try:
                item = self.inbox.get(timeout=max(0.01, end - time.monotonic()))
            except queue.Empty:
                break
            if item is self.CLOSED:
                items.append(item)
                break
            if item.get("type") != "PING":
                items.append(item)
        return items

    def expect_silence(self, seconds=1.0):
        items = self.collect(seconds)
        assert items == [], f"expected silence but received {items}"

    def expect_no_type(self, message_type, seconds=1.0):
        types = [m["type"] for m in self.collect(seconds) if m is not self.CLOSED]
        assert message_type not in types, f"unexpected {message_type} among {types}"

    # -- sending / closing ---------------------------------------------------------------------
    def send(self, data):
        self.ws.send_json(data)

    def send_text(self, text):
        self.ws.send_text(text)

    def close(self):
        try:
            self._cm.__exit__(None, None, None)
        except Exception:
            pass


@pytest.fixture
def live():
    opened = []

    def _open(client, path, headers=None):
        socket = LiveSocket(client, path, headers)
        opened.append(socket)
        return socket

    yield _open
    for socket in opened:
        socket.close()
