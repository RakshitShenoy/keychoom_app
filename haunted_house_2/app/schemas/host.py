from __future__ import annotations

from pydantic import BaseModel

from app.schemas.game import GameConfigOut


class HostPlayerOut(BaseModel):
    display_name: str
    pawn_color: str
    team_id: str | None
    is_connected: bool


class HostGameResponse(BaseModel):
    game_id: str
    game_code: str
    status: str
    team_count: int
    config: GameConfigOut
    capacity: int
    player_count: int
    players: list[HostPlayerOut]
