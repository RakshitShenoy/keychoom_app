# Escape the Haunted House: Code Documentation

Halloween multiplayer escape-room game. One host per game, 3-25 teams of 1-10 players, 3-8 floors, first team through the final door wins. This document describes the code as of Phase 13.

Stack: FastAPI (REST + WebSocket), SQLAlchemy 2 + Alembic (SQLite, portable to PostgreSQL), Pydantic v2, Jinja2 pages with plain HTML/CSS/JS. **One uvicorn worker on purpose**: locks, leases and WebSocket rooms live in process memory.

## 1. Core principles

1. **The server is authoritative.** Browsers send intents over REST; the server validates, commits to the database, then announces over WebSocket ("database first, broadcast second"). The WebSocket cannot change state (only `PING`, `PONG`, `REQUEST_STATE` are accepted).
2. **Identity comes from HttpOnly cookies**, never from request bodies. Tokens are random; only an HMAC-SHA256 hash is stored.
3. **Content is data, rules are code.** Rooms, puzzles, quizzes and dares are JSON files under `app/content/`; answers never leave the server.
4. **Positions vs. internal floors (Phase 13).** Everything players see and everything stored per team uses the *position* 1..N. A per-game hidden order maps positions to the 8 internal floor definitions.

## 2. Repository layout

```
app/
  main.py            application factory, router wiring, lifespan (content validation, sweeper)
  config.py          every tunable (env-driven, validated); TOTAL_FLOORS = 8
  errors.py          AppError(status, code, message, details);  error_handlers.py -> JSON errors
  auth/              tokens, sessions (cookies), dependencies (who is this?), hostplay, /api/me, rate limiter
  db/                base (naming convention, engine), session, types (UTCDateTime), enums, ids, models/, migrations/
  lobby/             create/join games, chat, formation state machine, events bus, views, pages
  teams/             team slots, leaders, team codes, readiness rules, join/kick/leave, queries
  game/              gameplay: service (rules), views (state builders), presets (content helpers),
                     leases (object locks), runtime (process state), router (HTTP)
  engines/           pure-Python rules: challenge (puzzles), quiz, dare
  hints/ timers/     penalty schedule; clock + deadline arithmetic
  jumpscares/        planning/firing service + asset registry
  ws/                manager (connections), hub (auth, snapshots, fan-out), protocol (envelopes), router
  host/              minimal host game view
  content/           presets/ puzzles/ quizzes/ dares/ (+ loader.py)
  schemas/           Pydantic request/response models
static/              css (dev, haunted, polish), js (common, realtime, host, player, game), audio/img/video
templates/           base, landing, host, player
tools/               build_content.py, validate_content.py, simulate_preset.py, multiplayer_check.py
scripts/start.sh     production entry point (migrations, then one uvicorn worker)
tests/               pytest suite (see section 12)
```

## 3. Game lifecycle

Stored in `games.status` (CHECK-constrained strings):

```
LOBBY --start formation--> TEAM_FORMATION --lock--> READY --start--> RUNNING --final door--> FINISHED
                                  ^------ reopen -------+
```

Public names: `WAITING`, `TEAM_FORMATION`, `READY_TO_START`. A team is `FORMING -> LOCKED -> PLAYING -> ESCAPED | ELIMINATED`.

* **Lobby / formation** (`app/lobby/formation.py`): chat is open only in `LOBBY`. Host approves a leader per team (`teams/service.py`), the leader's team gets a code `TEAM-XXXX`, players join by code. Locking requires readiness.
* **Readiness** (`teams/readiness.py`, pure functions): every one of `team_count` teams exists, has a leader and at least `min_players` (default **1**) and at most `max_players` players, and nobody is unassigned. **Smallest valid game: 3 teams x 1 player.** `start_game` re-checks readiness itself.
* **Start** (`game/service.py: start_game`): `READY -> RUNNING`, sets team deadlines, deals the position-1 room for every team, plans jumpscares.
* **Winning**: `open_final_door` decides the winner under a per-game lock; exactly one team wins.

## 4. Floors, order and fragments (Phase 13)

| Concept | Where |
|---|---|
| Number of floors (3-8, default 8) | `games.floor_count`, validated in `lobby/service.py: resolve_game_config` |
| Hidden order | `games.floor_order` (JSON list of distinct internal floors), drawn once in `create_game` by `presets.generate_floor_order` (`random.SystemRandom`) |
| Position -> internal floor | `presets.internal_floor(game, position)`; corrupt/legacy rows fall back to `1..count` (`floor_order_of`) |
| Player-visible numbers | `TeamFloorState.floor_no`, `Team.current_floor`: **positions** |
| Fragment ids/labels for players | `presets.player_fragment_id/label(position)`: "Floor 3 Key Fragment", never the internal floor |
| Final door | needs all N awarded fragments; code = N runes ordered by position (`presets.final_door_code`) |

Flow: `start_game` and `advance_floor` call `presets_for_floor(content, internal_floor(game, position))` and deal one of that floor's room presets (`assign_presets`, seeded per game+position). `_collected_presets` verifies each completed position used the room its stored order assigns. Nothing reshuffles the order: refresh, reconnect, restart and dashboard views only read it. Only the host dashboard shows it (`floor_plan`).

A room preset (`app/content/presets/*.json`) is a dependency graph of objects (puzzles, quizzes, dares, traps, decor) that leads to a 4-character vault, which awards the floor's fragment. Its internal chain is never altered; only whole floors are selected and ordered. `game/presets.py` also validates all content at startup (the app refuses to start on invalid content).

## 5. Gameplay rules (`game/service.py`)

* **Object leases** (`game/leases.py`): at most one player per team investigates an object at a time; leases expire (`OBJECT_LOCK_LEASE_SECONDS`), renewed by heartbeat. Per-team `asyncio`/thread locks (`GameRuntime.team_lock`) serialise a team's actions.
* **Challenges** (`engines/challenge.py`): normalised answer comparison, multi-step puzzles, partial results, wrong-answer cooldown. Answers are only in content files; `public_view` strips them.
* **Quizzes** (`engines/quiz.py`): server draws questions with a per-attempt seed, one at a time, server-side clock.
* **Dares** (`engines/dare.py`): safe, optional, confirmed in-game.
* **Vault**: the code unlocks when required objects are solved; correct code awards the fragment and completes the floor in one commit.
* **Hints**: 4 per team, penalties 30/60/90/120 s (`hints/penalties.py`), recorded in `hint_usage` with unique constraints; the deadline is recomputed server-side.
* **Timers** (`timers/deadline.py`): `deadline = start + base duration + hint penalties + paused time`. Clients only display. A sweeper (`service.sweep`) eliminates overdue teams and fires due jumpscares.
* **Pause/resume** (host): freezes every clock; on resume deadlines move forward by the pause length.
* **Jumpscares** (`jumpscares/`): planned per team at start; the server picks the asset, browsers only display; traps can trigger one for a single player.

## 6. Host "Play as Player" (Phase 13)

* Chosen at creation (`host_plays`). The host calls `POST /api/games/{code}/host/play` to create their own `Player` (`players.is_host_player = true`) with the normal name, capacity and team-code rules. Nobody is auto-assigned to a team.
* `auth/hostplay.py: host_is_playing` is true when the game is `CUTSCENE`/`RUNNING`, `host_plays` is set and the host has a player. While true:
  * `identify_viewer` never returns the host identity (lobby reads and sockets become the *player* view);
  * `/play/host` and `/api/host/game` answer `403 HOST_IS_PLAYING`;
  * the hub closes existing host-role sockets at `GAME_STARTED` (`HOST_IS_PLAYING`) and refuses new ones;
  * `start`, `pause`, `resume` return no whole-game data.
* The host session remains valid server-side (the game owner can still pause/resume via API); the player session has no host powers (`403 HOST_ONLY`).

## 7. REST API (all JSON; `{code}` = game code)

| Method + path | Who | Purpose |
|---|---|---|
| `POST /api/games` | anyone | create game (`team_count`, `max_players_per_team`, `base_duration_seconds`, `floor_count`, `host_plays`, ...) |
| `POST /api/games/join` | anyone | join with game code + display name |
| `GET /api/me` | any | restore identity (host/player sections) |
| `GET /api/games/{code}/lobby` | host or player | server-built lobby view (host sees ids/codes) |
| `POST .../team-formation/{start,lock,reopen}` | host | lobby state machine |
| `POST/DELETE .../teams/{team_id}/leader` | host | approve / remove leader |
| `POST .../teams/join` | player | join team by code |
| `POST .../players/{id}/kick`, `POST .../leave` | host / player | remove player |
| `POST .../chat` | player | public chat (only before formation) |
| `POST .../host/play` | host | host creates own player (only if `host_plays`) |
| `POST .../start` | host | `READY -> RUNNING` |
| `GET .../play/state` | player | the player's private floor state |
| `POST .../play/objects/{id}/{investigate,heartbeat,release,answer}` | player | object interaction |
| `POST .../play/objects/{id}/quiz/{start,answer}`, `.../dare/confirm` | player | quiz / dare |
| `POST .../play/{vault,hints,floor/next,final-door}` | player | vault, hints, climb, win |
| `POST .../pause`, `POST .../resume`, `GET .../play/host` | host | pause/resume, dashboard (`floor_plan`, teams, feed) |
| `GET /health`, `GET /`, `/host`, `/player` | anyone | health check, pages |

Errors: `{"error": {"code", "message", "details"}}` with stable codes (e.g. `REQUIREMENTS_NOT_MET`, `TEAM_FULL`, `FLOOR_NOT_COMPLETE`, `FINAL_DOOR_LOCKED`, `HOST_IS_PLAYING`). Authorization: the game code in a URL is only a claim, compared with the game the cookie belongs to (`403 WRONG_GAME`); ids from another game answer 404.

## 8. WebSocket (`/ws/games/{code}?role=host|player`)

Envelope: `{"type","id","ts","payload"}`; `LOBBY_STATE` and floor/dashboard snapshots carry a `version` (older ones are ignored by the client). Server messages: `LOBBY_STATE`, `FLOOR_STATE` (players, own team only), `HOST_DASHBOARD` (host only), lobby/gameplay events (see `LobbyEventType`: `PLAYER_JOINED`, `TEAM_JOINED`, `GAME_STARTED`, `OBJECT_SOLVED`, `FRAGMENT_AWARDED`, `FLOOR_COMPLETED`, `HINT_USED`, `TEAM_ELIMINATED`, `FINAL_DOOR_OPENED`, `GAME_PAUSED`, `JUMPSCARE`, ...), `PING/PONG`, `ERROR`. Fatal error codes (no reconnect): `NOT_AUTHENTICATED`, `NOT_AUTHORIZED`, `KICKED`, `HOST_IS_PLAYING`.

* `ws/hub.py` authenticates with the same resolver as REST (`identify_viewer`), checks `Origin`, builds personalised snapshots (one DB load per push, coalesced by a debounce) and routes events from `LobbyEventBus`: team events go to the host and that one team only.
* `ws/manager.py` is a pure-Python registry keyed by game (isolation); slow consumers are dropped and resync on reconnect. Presence (`players.is_connected`) is separate from membership: refresh or reconnect never removes a player.
* Heartbeat: server pings every `WS_HEARTBEAT_INTERVAL_SECONDS`, closes silent connections. The client (`static/js/realtime.js`) reconnects with capped backoff and falls back to REST polling.

## 9. Database

Tables: `games`, `players`, `teams`, `team_floor_state`, `object_state`, `hint_usage`, `quiz_attempts`, `dare_confirmations`, `jumpscare_events`, `chat_messages`, `game_events` (append-only audit log). Portable types only; CHECK constraints guard status vocabularies and ranges (`floor_no` 1-8, `floor_count` 3-8, hints 1-4); unique constraints stop duplicate floors, objects, hints and dares even if code has a bug.

Migrations (Alembic, batch mode for SQLite): `0001` initial, `0002` team formation, `0003` host pause, `0004` floor count / order / host play (existing games become 8-floor, original order, host only). A test fails if models and migrations drift.

## 10. Frontend

Development-style pages with the Halloween theme: `templates/` + `static/css/{dev,haunted,polish}.css`. All text is inserted with `textContent`. The browser never decides state: `host.js` (create form with floor count and host gameplay, lobby, dashboard), `player.js` (join, teams, chat), `game.js` (room renderer, HUD, puzzles, final door), `realtime.js` (socket + fallback poll).

## 11. Configuration and deployment

`app/config.py` reads environment variables / `.env` (see `.env.example`). Key values: `SECRET_KEY` (>= 32 chars in production), `DATABASE_URL`, `COOKIE_SECURE`, team/player/duration limits, hint penalties, lease and heartbeat timings, rate limits. Contradictory settings stop the server from starting.

Production: `scripts/start.sh` runs `alembic upgrade head`, then `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1 --proxy-headers`. Render needs no extra configuration for Phase 13.

## 12. Tests and tools

`pytest` (about 487 tests, 18 browser tests need Playwright + Chromium and are skipped otherwise). Notable modules: `test_phase12_minimum.py` (3x1 rule), `test_phase13_floors.py` (floor count, random hidden order, host play, leak and security checks, migration), WebSocket, security, concurrency, content and migration tests. Phase 13 tests use the real shuffle (`@pytest.mark.random_floors`); all other modules pin the order to `1..N` through an autouse fixture so room-specific assertions stay valid.

Tools: `tools/validate_content.py`, `tools/simulate_preset.py` (bot solves each preset), `tools/multiplayer_check.py --base URL --teams N --per-team M` (independent clients and sockets against a running server; must be run from a checkout).

## 13. Known limits

* Single process by design: several workers would need shared pub/sub and shared locks.
* A connected socket is not re-validated when its cookie expires (except kicks/leaves).
* Room titles and decor are visible to players (they are the gameplay); only ids, preset names and internal floor numbers are hidden.
* Browser behaviour (host form, host-player card, redirects) is covered by API tests and the Playwright suite, which must be run on a machine with Chromium.
