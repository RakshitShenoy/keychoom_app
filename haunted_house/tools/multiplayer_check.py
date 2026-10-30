#!/usr/bin/env python3
"""Multi-client smoke test against a RUNNING server (local or deployed).

    python tools/multiplayer_check.py --base https://your-app.replit.app --teams 3 --per-team 3

It plays the whole lobby flow with many independent clients (each has its own cookie jar and its own
WebSocket, exactly like separate browsers/devices) and then lets every team solve Floor 1 at the same
time. It reports every HTTP status it saw, so a 502/5xx or a dropped WebSocket shows up as a failure with
the exact request. Exit code 0 = everything passed.

Needs: pip install httpx websockets   (both are already installed with requirements-dev.txt)
Floor 1 has a single fixed room, which is why its answers are known here.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter

import httpx
import websockets

FLOOR1 = [("candle", "3"), ("painting", "maid"), ("vent", "34")]
VAULT = "K7R4"


class Report:
    def __init__(self) -> None:
        self.statuses: Counter[int] = Counter()
        self.failures: list[str] = []
        self.slow: list[tuple[float, str]] = []  # requests that took more than 3 seconds

    def seen(self, response: httpx.Response, what: str, expect: tuple[int, ...] = (200, 201)) -> httpx.Response:
        self.statuses[response.status_code] += 1
        if response.status_code not in expect:
            self.failures.append(f"{what}: HTTP {response.status_code} {response.text[:160]}")
        return response

    def check(self, ok: bool, what: str) -> None:
        if not ok:
            self.failures.append(what)


class Client:
    """One browser: its own cookies, its own WebSocket."""

    def __init__(self, base: str, name: str, report: Report) -> None:
        self.base, self.name, self.report = base.rstrip("/"), name, report
        self.http = httpx.AsyncClient(base_url=self.base, timeout=120, follow_redirects=True)
        self.ws = None
        self.messages: list[dict] = []
        self.reader: asyncio.Task | None = None
        self.code = ""
        self.closed_with = ""

    async def call(self, method: str, path: str, body=None, expect=(200, 201)) -> httpx.Response:
        started = time.monotonic()
        r = await self.http.request(method, path, json=body if body is not None else None)
        took = time.monotonic() - started
        if took > 3:
            self.report.slow.append((round(took, 1), f"{method} {path.split('/api/games/')[-1][7:] or path}"))
        return self.report.seen(r, f"{self.name} {method} {path}", expect)

    async def open_socket(self, code: str, role: str) -> None:
        scheme = "wss" if self.base.startswith("https") else "ws"
        url = f"{scheme}://{self.base.split('://', 1)[1]}/ws/games/{code}?role={role}"
        cookie = "; ".join(f"{c.name}={c.value}" for c in self.http.cookies.jar)
        headers = {"Cookie": cookie, "Origin": self.base}
        try:
            self.ws = await websockets.connect(url, additional_headers=headers, open_timeout=20)
        except TypeError:  # older websockets releases
            self.ws = await websockets.connect(url, extra_headers=headers, open_timeout=20)
        self.reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                message = json.loads(raw)
                if message.get("type") == "PING":
                    await self.ws.send(json.dumps({"type": "PONG"}))
                self.messages.append(message)
        except Exception as exc:  # remember WHY the socket ended (close code and server error message)
            self.closed_with = f"{type(exc).__name__}: {str(exc)[:80]}"
        finally:
            if self.ws is not None and getattr(self.ws, "close_code", None) is not None:
                self.closed_with = f"close {self.ws.close_code} {self.ws.close_reason}"
            errors = [m.get("code") for m in self.messages if m.get("type") == "ERROR"]
            if errors:
                self.closed_with += f" server-error={errors[-1]}"

    def got(self, message_type: str) -> list[dict]:
        return [m for m in self.messages if m.get("type") == message_type]

    async def wait_for(self, predicate, timeout: float = 20.0) -> dict | None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for m in self.messages:
                if predicate(m):
                    return m
            await asyncio.sleep(0.1)
        return None

    @property
    def alive(self) -> bool:
        return self.ws is not None and self.reader is not None and not self.reader.done()

    async def close(self) -> None:
        if self.ws:
            await self.ws.close()
        await self.http.aclose()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--teams", type=int, default=3)
    ap.add_argument("--per-team", type=int, default=3)
    ap.add_argument("--minutes", type=int, default=30)
    args = ap.parse_args()
    report, t0 = Report(), time.monotonic()
    n_players = args.teams * args.per_team

    host = Client(args.base, "host", report)
    r = await host.call("POST", "/api/games", {
        "team_count": args.teams, "min_players_per_team": min(3, args.per_team),
        "max_players_per_team": max(3, args.per_team), "base_duration_seconds": args.minutes * 60})
    if r.status_code != 201:
        print("could not create a game:", r.status_code, r.text[:300])
        return 1
    code = r.json()["game_code"]
    print(f"game {code}: {args.teams} teams x {args.per_team} players = {n_players} players on {args.base}")

    players = [Client(args.base, f"p{i + 1}", report) for i in range(n_players)]
    await asyncio.gather(*[p.call("POST", "/api/games/join", {"game_code": code, "display_name": f"Player {i + 1}"})
                           for i, p in enumerate(players)])
    for p in players:
        p.code = code
    await host.open_socket(code, "host")
    await asyncio.gather(*[p.open_socket(code, "player") for p in players])
    await asyncio.gather(*[p.wait_for(lambda m: m["type"] == "LOBBY_STATE") for p in players])
    report.check(all(p.got("LOBBY_STATE") for p in players), "every player received LOBBY_STATE over the WebSocket")
    report.check(all(p.alive for p in players), "all player WebSockets stay open at once")
    lobby = (await host.call("GET", f"/api/games/{code}/lobby")).json()
    report.check(lobby["counts"]["player_count"] == n_players, "all players appear in the host lobby")

    print(f"  [{time.monotonic() - t0:5.1f}s] joined + sockets open")
    await host.call("POST", f"/api/games/{code}/team-formation/start")
    pid_by_name = {p["display_name"]: p["player_id"] for p in (await host.call("GET", f"/api/games/{code}/lobby")).json()["players"]}
    teams = [t["team_id"] for t in (await host.call("GET", f"/api/games/{code}/lobby")).json()["teams"]]
    groups = [players[i * args.per_team:(i + 1) * args.per_team] for i in range(args.teams)]
    for team_id, group in zip(teams, groups):
        await host.call("POST", f"/api/games/{code}/teams/{team_id}/leader", {"player_id": pid_by_name[group[0].name.replace("p", "Player ")]})
    codes = {t["team_id"]: t["team_code"] for t in (await host.call("GET", f"/api/games/{code}/lobby")).json()["teams"]}
    await asyncio.gather(*[m.call("POST", f"/api/games/{code}/teams/join", {"team_code": codes[team_id]})
                           for team_id, group in zip(teams, groups) for m in group[1:]])
    print(f"  [{time.monotonic() - t0:5.1f}s] leaders + team joins done")
    await host.call("POST", f"/api/games/{code}/team-formation/lock")
    await host.call("POST", f"/api/games/{code}/start")

    print(f"  [{time.monotonic() - t0:5.1f}s] game started")
    states = await asyncio.gather(*[p.call("GET", f"/api/games/{code}/play/state") for p in players])
    report.check(all(s.status_code == 200 and s.json()["state"]["room"]["floor"] == 1 for s in states), "every player is on Floor 1")
    report.check(all("K7R4" not in s.text for s in states), "no player state contains the vault code")
    dash = await host.wait_for(lambda m: m["type"] == "HOST_DASHBOARD")
    report.check(dash is not None and len(dash["payload"]["teams"]) == args.teams, "host received the live dashboard over the WebSocket")
    report.check(all(not p.got("HOST_DASHBOARD") for p in players), "no player received the host dashboard")

    async def solve_floor1(group: list[Client]) -> None:
        a, b, c = group[0], group[1 % len(group)], group[2 % len(group)]
        for owner, (obj, answer) in zip((a, b, c), FLOOR1):
            await owner.call("POST", f"/api/games/{code}/play/objects/{obj}/investigate")
            r = await owner.call("POST", f"/api/games/{code}/play/objects/{obj}/answer", {"answer": answer})
            report.check(r.status_code == 200 and r.json()["result"]["result"] == "CORRECT", f"{owner.name} solved {obj}")
        r = await a.call("POST", f"/api/games/{code}/play/vault", {"code": VAULT})
        report.check(r.status_code == 200 and r.json()["result"]["result"] == "CORRECT", "vault opened")
        await b.call("POST", f"/api/games/{code}/play/floor/next")

    print(f"  [{time.monotonic() - t0:5.1f}s] states read, dashboard received")
    await asyncio.gather(*[solve_floor1(g) for g in groups])  # every team plays at the same moment
    await asyncio.sleep(1.0)
    dash_state = (await host.call("GET", f"/api/games/{code}/play/host")).json()
    report.check(all(t["floor"] == 2 and t["fragments"] == 1 for t in dash_state["teams"]), "host sees every team on Floor 2 with 1 fragment")
    live = max((m for m in host.got("HOST_DASHBOARD")), key=lambda m: m["version"])
    report.check(all(t["floor"] == 2 for t in live["payload"]["teams"]), "the pushed dashboard (no refresh) shows every team on Floor 2")

    pause = await host.call("POST", f"/api/games/{code}/pause", {})
    report.check(pause.status_code == 200 and pause.json()["paused"], "host paused the game")
    r = await players[0].call("POST", f"/api/games/{code}/play/hints", {"level": 1}, expect=(409,))
    report.check(r.status_code == 409 and r.json()["error"]["code"] == "GAME_PAUSED", "players are rejected while paused")
    await host.call("POST", f"/api/games/{code}/resume", {})

    dead = [p for p in players if not p.alive]
    if dead:
        reasons = Counter(p.closed_with or "unknown" for p in dead)
        print(f"WebSockets that ended early: {len(dead)} -> {dict(reasons)}")
    report.check(all(p.alive for p in players) and host.alive, "all WebSockets were still connected at the end")
    for c in [host, *players]:
        await c.close()

    seconds = time.monotonic() - t0
    print(f"HTTP statuses seen: {dict(sorted(report.statuses.items()))}")
    bad = {k: v for k, v in report.statuses.items() if k >= 500}
    print(f"5xx responses: {sum(bad.values())}   duration: {seconds:.1f}s   websockets: {n_players + 1}")
    if report.slow:
        worst = sorted(report.slow, reverse=True)[:5]
        print(f"slow requests (>3s): {len(report.slow)}; slowest: {worst}")
    if report.failures:
        print("FAILED:")
        for f in report.failures[:30]:
            print("  -", f)
        return 1
    print("PASS: lobby flow, teams, start, concurrent Floor 1 play, live host dashboard and pause all worked.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
