"""Real-browser end-to-end support: a live uvicorn server (migrated database, real cookies, real
WebSockets) plus API 'players' that stand in for the teammates of the player under test."""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from play_support import Play, load_library  # noqa: E402

LIBRARY = load_library()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveServer:
    """uvicorn in a subprocess on a database created by the real Alembic migrations."""

    def __init__(self, tmp_path: Path, **env_extra: str):
        self.tmp_path = tmp_path
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.env = {
            **os.environ, "APP_ENV": "development", "DATABASE_URL": f"sqlite:///{tmp_path / 'e2e.db'}",
            "SECRET_KEY": "e2e-secret-key", "ANSWER_COOLDOWN_SECONDS": "0.3", "JUMPSCARES_PER_TEAM": "3",
            "JUMPSCARE_COOLDOWN_SECONDS": "90", "WS_HEARTBEAT_INTERVAL_SECONDS": "5", "WS_HEARTBEAT_TIMEOUT_SECONDS": "20",
            **env_extra,
        }
        self.proc: subprocess.Popen | None = None
        self.log = tmp_path / f"server_{self.port}.log"

    def migrate(self) -> None:
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=self.env, check=True, capture_output=True)

    def start(self) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(self.port), "--log-level", "warning"],
            cwd=ROOT, env=self.env, stdout=open(self.log, "ab"), stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                if httpx.get(self.base + "/health", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(0.2)
        raise RuntimeError("server did not start: " + self.log.read_text()[-2000:])

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def restart(self) -> None:
        self.stop()
        self.start()


class Handle:
    def __init__(self, client: httpx.Client, player_id: str, name: str):
        self.client, self.id, self.name = client, player_id, name


class E2ELobby:
    """Same surface as tests.conftest.LobbyHarness, over real HTTP - so the bot solver works unchanged."""

    def __init__(self, server: LiveServer, team_count=3, min_players=3, max_players=5, duration_seconds=3600):
        self.server = server
        self.host = httpx.Client(base_url=server.base, timeout=20)
        body = self.host.post("/api/games", json={
            "team_count": team_count, "min_players_per_team": min_players, "max_players_per_team": max_players,
            "base_duration_seconds": duration_seconds}).json()
        self.game, self.code = body, body["game_code"]
        self.players: list[Handle] = []

    def url(self, suffix): return f"/api/games/{self.code}{suffix}"

    def join(self, name):
        client = httpx.Client(base_url=self.server.base, timeout=20)
        r = client.post("/api/games/join", json={"game_code": self.code, "display_name": name})
        assert r.status_code == 201, r.text
        h = Handle(client, r.json()["player_id"], name)
        self.players.append(h)
        return h

    def lobby(self): return self.host.get(self.url("/lobby")).json()
    def team(self, slot): return next(t for t in self.lobby()["teams"] if t["slot_no"] == slot)
    def team_code(self, slot): return self.team(slot)["team_code"]
    def start_formation(self): assert self.host.post(self.url("/team-formation/start")).status_code == 200
    def assign(self, slot, player): return self.host.post(self.url(f"/teams/{self.team(slot)['team_id']}/leader"), json={"player_id": player.id})
    def join_team(self, player, code): return player.client.post(self.url("/teams/join"), json={"team_code": code})
    def lock(self): return self.host.post(self.url("/team-formation/lock"))
    def start(self):
        r = self.host.post(self.url("/start"))
        assert r.status_code == 200, r.text

    def play(self, handle) -> Play:
        return Play(self, handle)


def api_play(client_handle, lobby):
    return Play(lobby, client_handle)
