from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class JoinGameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_code: str
    display_name: str


class JoinGameResponse(BaseModel):
    player_id: str
    game_id: str
    game_code: str
    display_name: str
    team_id: str | None
    pawn_color: str
    status: str
    session_established: bool
    session_expires_at: datetime


class LobbyPlayerOut(BaseModel):
    display_name: str
    pawn_color: str
    team_id: str | None


class PlayerLobbyResponse(BaseModel):
    game_code: str
    status: str
    you: LobbyPlayerOut
    players: list[LobbyPlayerOut]
