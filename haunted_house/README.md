# Escape the Haunted House

A Halloween-themed, competitive, real-time multiplayer escape room that runs in the browser.
One **host** runs the game; **3-25 teams** of **3-10 players** race through an eight-floor haunted house.
Each floor is a room of connected puzzles that ends in a vault. The vault gives a **key fragment**; with all
eight fragments a team can open the **final door**. The first team through the final door wins.

> **Scope:** this is the state after **Phases 1-11** (backend, lobby, real-time layer, gameplay, hints,
> timers, content for 8 floors, UI, jumpscares, host dashboard, integration testing and deployment
> configuration). **Phase 12 has not been started.** See "Known limitations" and "What has and has not
> been verified" before relying on it for a live event.

## Features

* **Lobby and teams:** game codes, display names, public lobby chat, host-approved team leaders, team
  codes, locking of team membership, server-side readiness rules, kick/leave.
* **Gameplay:** 8 floors (15 room presets, 2 per floor from floor 2), puzzles, quizzes, dares, trap
  objects, per-object locks so one teammate works on an object at a time, vaults, key fragments, final door.
* **Hints and timers:** at most 4 hints per team with growing time penalties; every deadline is computed and
  enforced by the server; teams that run out of time are eliminated.
* **Horror:** server-scheduled jumpscares (about 5 s, with cooldown), trap objects, reduced-effects option.
* **Host dashboard:** live overview of every team (floor, room, current object/task, members, connection
  state, fragments, hints, penalties, time left/elapsed, status), a tower progress map, an activity feed,
  **pause/resume**, kicking players, and the winner view with completion time.
* **Server authority:** answers, vault codes and hint text exist only on the server. Clients send intents;
  the server decides. The winner is decided atomically, so exactly one team can win.

## Architecture

| Part | Technology |
|---|---|
| Web/API/WebSocket | FastAPI + uvicorn, **one worker process** |
| Database | SQLite via SQLAlchemy 2 + Alembic migrations (portable types) |
| Frontend | server-rendered Jinja2 pages + plain JavaScript (no build step) |
| Live updates | one WebSocket per browser: `/ws/games/{code}?role=host|player` |
| Content | JSON files under `app/content/` (presets, puzzles, quizzes, dares), validated at startup |

State changes are made with REST calls, validated and committed to the database, and then **announced** over
the WebSocket. The browser may only send `PING`, `PONG` and `REQUEST_STATE` over the socket.
Per-team state and the host's dashboard are pushed to the right recipients only (players never receive the
host dashboard). Live locks, WebSocket rooms and the sweeper live in the process, which is why exactly one
worker must be used.

```
app/            main.py, config.py, auth/, lobby/, teams/, game/ (service, views, leases, router),
                hints/, timers/, jumpscares/, content/, ws/ (hub, manager, router, protocol), host/, db/
static/ templates/   frontend (JS, CSS, images)
tests/          pytest suite; tests/browser = real Chromium tests
tools/          multiplayer_check.py, validate_content.py, build_content.py, puzzle_families.py
scripts/start.sh    production/Replit entry point
```

## Local setup

Requires Python 3.11+ (developed on 3.12).

```bash
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt                      # runtime + test tools
cp .env.example .env                                     # then edit .env (never commit it)
alembic upgrade head                                     # create/upgrade the SQLite database
uvicorn app.main:app --port 8000                         # open http://localhost:8000
```

Use `python -c "import secrets; print(secrets.token_urlsafe(48))"` to make a `SECRET_KEY`.
Never use `--workers N` (N > 1) and avoid `--reload` during a real game: either would split or reset the
in-memory state. Health check: `GET /health` returns `{"status":"ok"}`.

## Environment variables

Every setting is in `.env.example` with its default. The important ones:

| Variable | Meaning |
|---|---|
| `APP_ENV` | `development`, `test` or `production`. Production **refuses to start** without a private `SECRET_KEY` of 32+ characters |
| `SECRET_KEY` | keyed hashing of session tokens. Required in production; set it in Replit Secrets, never in a file |
| `DATABASE_URL` | default `sqlite:///./haunted_house.db` |
| `COOKIE_SECURE` | `true` over HTTPS. Cookies are also marked `Secure` automatically when a request arrives over HTTPS (behind Replit's proxy) |
| `WS_ALLOWED_ORIGINS` | extra allowed WebSocket `Origin` hosts (normally empty: same-origin only) |
| `MIN/MAX_TEAMS_LIMIT`, `MIN/MAX_PLAYERS_LIMIT` | global limits (3-25 teams, 3-10 players) |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` | database pool; must be at least as large as the ~40 web worker threads |
| `PORT`, `LOG_LEVEL` | used by `scripts/start.sh` |

Settings that contradict each other stop the server from starting.

## Database migrations

```bash
alembic upgrade head      # apply all migrations (0001 schema, 0002 teams, 0003 host pause)
alembic current           # show the applied revision
alembic downgrade -1      # step back one revision (development only)
```
After changing a model: `alembic revision --autogenerate -m "..."`, read the generated file, then upgrade.
A test fails if the models and migrations drift apart. Migration 0003 is additive and keeps existing games.

## Running the tests

```bash
pytest -q                          # whole suite (API, WebSocket, concurrency, migrations, deployment)
pytest -q --ignore=tests/browser   # without the real-browser tests
playwright install chromium        # once, for tests/browser (they skip if no browser can start)
```
The browser tests drive real Chromium against a live uvicorn server (refresh, reconnect, host dashboard,
pause, kick, winner). Playwright's version must match the installed Chromium build.

## Multiplayer checker (many independent clients)

`tools/multiplayer_check.py` talks to a **running** server (local or deployed) with many clients that each
have their own cookies and WebSocket, plays the lobby flow, solves Floor 1 for every team at the same time,
checks the live host dashboard and pause, and reports every HTTP status it saw.

```bash
python tools/multiplayer_check.py --base http://127.0.0.1:8000 --teams 3 --per-team 3
python tools/multiplayer_check.py --base https://YOUR-APP.replit.app --teams 5 --per-team 4
```
Exit code 0 means everything passed; any 5xx, dropped WebSocket or wrong state is listed. **Run this against your
deployed URL before the event**; it is the quickest way to see a proxy problem such as a 502.
(`--teams 3 --per-team 1` needs the server started with `MIN_PLAYERS_LIMIT=1`; the game normally requires 3.)

## Replit deployment

1. Import the project (from GitHub or the ZIP) into a Python Repl. `.replit` and `scripts/start.sh` are included.
2. In **Tools -> Secrets** add `APP_ENV=production` and a private `SECRET_KEY` (32+ characters). Optionally
   `COOKIE_SECURE=true`. Do not put secrets in files.
3. Press Run to test in the workspace, then **Publish** as a **Reserved VM** (one always-on instance).
   Do not use Autoscale with more than one instance: live state is in one process.
4. `scripts/start.sh` runs `alembic upgrade head` and then
   `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1 --proxy-headers --forwarded-allow-ips='*'`,
   so migrations are applied on every start and the server binds to the interface and port Replit provides.
5. Open the published URL, use **Host** on one device and **Player** on others.

**WebSocket requirements:** the host/proxy must allow WebSocket upgrades (Replit does). Over HTTPS the page
automatically uses `wss://` (the URL is built from `window.location`, nothing is hard-coded). Share one exact
URL with everyone: the Origin check only accepts the page's own host.

**Back up the SQLite file before an event** (`haunted_house.db`). Whether the file survives a redeploy depends
on the Replit deployment type and has not been tested here.

## Known limitations

* **Single process.** All players of all games share one server process; there is no horizontal scaling.
  A restart keeps the database state (sessions, teams, progress, deadlines) but releases all object locks.
* **Large lobbies are heavy.** Every lobby change pushes a full personal snapshot to every connected client.
  At 250 clients that is about 0.4 s of CPU and 13 MB per push, so the push delay grows with the number of
  connections (about 2 s at 250). Gameplay messages are small; this only affects the pre-game lobby.
* **250-player load test (1 CPU shared by server and test client):** all HTTP calls succeed (0 server errors),
  every player stays connected, 25 teams play Floor 1 simultaneously and the host dashboard updates live.
  Under that load 225 simultaneous team joins take roughly 10-15 seconds because joins are deliberately
  serialised. A 1-CPU sandbox is not a model of Replit hardware; measure on your real deployment.
* **Browser tab frozen/offline:** the page shows a connection badge and reconnects automatically with
  backoff (12 attempts, then a click-to-retry). Server state always replaces client state.
* A connected socket is not re-validated when its session cookie expires (12 h by default) unless the player
  is kicked or leaves.
* `games.winner_team_id` has no database foreign key.
* Deployment values in `.replit` (`deploymentTarget`, `build`) follow Replit's documented format but have not
  been exercised on a real Replit account.

## What has and has not been verified

Verified in the development sandbox: the automated suite, real-Chromium tests, the multiplayer checker at
several sizes up to 250 players against a real uvicorn server, production start (`scripts/start.sh`) with a
fresh database, and secure cookies behind a simulated HTTPS proxy.

**Not verified:** anything on the actual Replit URL (real devices, Replit's proxy, deployment persistence).
An earlier public-tunnel test showed a 502 for a second player; the application-level causes found since
(database pool exhaustion, snapshot overload, WebSocket start-up queue) are fixed, but that specific tunnel
failure has **not** been re-tested and may also come from the tunnel service itself.
