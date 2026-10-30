"""Centralised configuration.

Every tunable number lives here and can be overridden with an environment
variable (or a local, uncommitted ``.env`` file). Nothing else in the code base
should hard-code limits, hint penalties, lease durations and so on.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = BASE_DIR / "app"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

DEFAULT_DEV_SECRET = "dev-only-insecure-secret-change-me"

# The game design fixes the building at 8 floors; used by DB check constraints.
TOTAL_FLOORS = 8


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- environment -------------------------------------------------------
    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite:///./haunted_house.db"
    secret_key: str = DEFAULT_DEV_SECRET
    content_dir: Path = APP_DIR / "content"

    # --- database connection pool (must cover the ~40 web worker threads; see app/db/base.py) ---
    db_pool_size: int = Field(50, ge=5)
    db_max_overflow: int = Field(50, ge=0)
    db_pool_timeout_seconds: float = Field(30, ge=1)

    # --- cookies -----------------------------------------------------------
    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    host_cookie_name: str = "hh_host"
    player_cookie_name: str = "hh_player"

    # --- global game limits -------------------------------------------------
    min_teams_limit: int = Field(3, ge=1)
    max_teams_limit: int = Field(25, ge=1)
    min_players_limit: int = Field(3, ge=1)
    max_players_limit: int = Field(10, ge=1)
    min_base_duration_seconds: int = Field(300, ge=1)
    max_base_duration_seconds: int = Field(14400, ge=1)
    default_base_duration_seconds: int = Field(3600, ge=1)

    # --- hints ----------------------------------------------------------------
    max_hints: int = Field(4, ge=0)
    # Comma separated seconds, one entry per hint (hint 1 first).
    hint_penalties_seconds: str = "30,60,90,120"

    # --- realtime (used from Phase 2) -----------------------------------------
    object_lock_lease_seconds: int = Field(75, ge=5)
    ws_heartbeat_interval_seconds: int = Field(20, ge=1)
    ws_heartbeat_timeout_seconds: int = Field(60, ge=2)

    # --- gameplay (Phase 4-6) ---------------------------------------------------------
    answer_cooldown_seconds: float = Field(2.0, ge=0)  # pause after a wrong answer / wrong vault code
    sweeper_interval_seconds: float = Field(1.0, gt=0)  # lease expiry, team deadlines, quiz timeouts

    # --- jumpscares (Phase 9) ----------------------------------------------------------------
    jumpscares_enabled: bool = True
    jumpscares_per_team: int = Field(3, ge=0, le=12)      # scheduled randomly over a whole game
    jumpscare_cooldown_seconds: float = Field(90.0, ge=0)  # minimum gap between two scares of one team
    jumpscare_display_ms: int = Field(5000, ge=1000, le=10000)

    # --- WebSocket layer (Phase 3) ---------------------------------------------------
    ws_snapshot_debounce_seconds: float = Field(0.05, ge=0)  # coalesces bursts of changes
    # Every change rebuilds a personal snapshot for EVERY connected client (~0.4s of CPU at 250 clients).
    # The wait before a push therefore grows with the number of connections: bursts collapse into
    # one push instead of saturating the server. 250 clients -> about 2 seconds.
    ws_snapshot_cost_per_connection_seconds: float = Field(0.008, ge=0)
    ws_dashboard_debounce_seconds: float = Field(0.25, ge=0)  # coalesces host-dashboard pushes
    ws_max_message_bytes: int = Field(4096, ge=64)
    ws_send_queue_size: int = Field(200, ge=10)
    ws_state_request_limit: int = Field(5, ge=1)  # REQUEST_STATE messages per window
    ws_state_request_window_seconds: int = Field(10, ge=1)
    ws_check_origin: bool = True
    ws_allowed_origins: str = ""  # extra allowed Origin hosts, comma separated

    # --- anonymous sessions -------------------------------------------------------
    host_session_ttl_hours: int = Field(12, ge=1)
    player_session_ttl_hours: int = Field(12, ge=1)

    # --- identity rules ------------------------------------------------------------
    min_display_name_length: int = Field(2, ge=1)
    # The database column is 32 characters wide.
    max_display_name_length: int = Field(24, ge=1, le=32)
    game_code_length: int = Field(6, ge=4, le=12)

    # --- team formation (Phase 2) ---------------------------------------------------
    team_code_length: int = Field(4, ge=3, le=6)
    team_code_failure_limit: int = Field(10, ge=1)
    team_code_failure_window_seconds: int = Field(60, ge=1)

    # --- lobby chat (Phase 2) ---------------------------------------------------------
    chat_max_length: int = Field(300, ge=1, le=500)  # DB column is 500 wide
    chat_history_limit: int = Field(50, ge=1, le=200)
    chat_rate_limit_messages: int = Field(5, ge=1)
    chat_rate_limit_window_seconds: int = Field(10, ge=1)

    # --- abuse protection -------------------------------------------------------------
    join_failure_limit: int = Field(15, ge=1)
    join_failure_window_seconds: int = Field(60, ge=1)

    # ------------------------------------------------------------------------------
    @property
    def hint_penalty_schedule(self) -> tuple[int, ...]:
        return tuple(int(p) for p in self.hint_penalties_seconds.split(",") if p.strip())

    @property
    def ws_extra_origins(self) -> frozenset[str]:
        return frozenset(o.strip().lower() for o in self.ws_allowed_origins.split(",") if o.strip())

    @property
    def host_session_ttl_seconds(self) -> int:
        return self.host_session_ttl_hours * 3600

    @property
    def player_session_ttl_seconds(self) -> int:
        return self.player_session_ttl_hours * 3600

    @model_validator(mode="after")
    def _check_consistency(self) -> "Settings":
        if self.min_teams_limit > self.max_teams_limit:
            raise ValueError("MIN_TEAMS_LIMIT must be <= MAX_TEAMS_LIMIT")
        if self.min_players_limit > self.max_players_limit:
            raise ValueError("MIN_PLAYERS_LIMIT must be <= MAX_PLAYERS_LIMIT")
        if self.min_base_duration_seconds > self.max_base_duration_seconds:
            raise ValueError("MIN_BASE_DURATION_SECONDS must be <= MAX_BASE_DURATION_SECONDS")
        if not (
            self.min_base_duration_seconds
            <= self.default_base_duration_seconds
            <= self.max_base_duration_seconds
        ):
            raise ValueError("DEFAULT_BASE_DURATION_SECONDS must be within the duration limits")
        if self.min_display_name_length > self.max_display_name_length:
            raise ValueError("MIN_DISPLAY_NAME_LENGTH must be <= MAX_DISPLAY_NAME_LENGTH")
        try:
            schedule = self.hint_penalty_schedule
        except ValueError as exc:
            raise ValueError("HINT_PENALTIES_SECONDS must be comma separated integers") from exc
        if len(schedule) != self.max_hints:
            raise ValueError("HINT_PENALTIES_SECONDS must contain exactly MAX_HINTS entries")
        if any(p < 0 for p in schedule):
            raise ValueError("HINT_PENALTIES_SECONDS must not contain negative values")
        if self.ws_heartbeat_timeout_seconds <= self.ws_heartbeat_interval_seconds:
            raise ValueError("WS_HEARTBEAT_TIMEOUT_SECONDS must exceed the heartbeat interval")
        if self.app_env == "production" and (
            self.secret_key in {DEFAULT_DEV_SECRET, "change-me"} or len(self.secret_key) < 32
        ):
            raise ValueError(
                "SECRET_KEY must be a private random value of at least 32 characters in production"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
