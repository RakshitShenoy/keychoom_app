from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, StrictInt


class GameCreateRequest(BaseModel):
    """All fields optional: omitted values fall back to the configured defaults/limits."""

    model_config = ConfigDict(extra="forbid")

    min_teams: StrictInt | None = None
    max_teams: StrictInt | None = None
    min_players_per_team: StrictInt | None = None
    max_players_per_team: StrictInt | None = None
    base_duration_seconds: StrictInt | None = None
    team_count: StrictInt | None = None  # exact number of team slots (default: max_teams)


class GameConfigOut(BaseModel):
    min_teams: int
    max_teams: int
    min_players_per_team: int
    max_players_per_team: int
    base_duration_seconds: int


class GameCreateResponse(BaseModel):
    game_id: str
    game_code: str
    status: str
    host_session_established: bool
    host_session_expires_at: datetime
    team_count: int
    config: GameConfigOut
