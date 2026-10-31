from __future__ import annotations

from pydantic import BaseModel

from app.schemas.game import GameConfigOut


class MeHost(BaseModel):
    game_id: str
    game_code: str
    status: str
    team_count: int
    config: GameConfigOut
    player_count: int


class MePlayer(BaseModel):
    player_id: str
    display_name: str
    pawn_color: str
    team_id: str | None
    game_id: str
    game_code: str
    status: str


class MeResponse(BaseModel):
    """Both sections are independent: a browser may hold a host and a player session."""

    host: MeHost | None
    player: MePlayer | None
