from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class LeaderAssignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    player_id: str


class TeamJoinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    team_code: str


class ChatSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str


class ProblemOut(BaseModel):
    code: str
    message: str
    team_id: str | None = None


class LobbyTeamOut(BaseModel):
    team_id: str
    slot_no: int
    name: str
    icon: str | None
    leader_name: str | None
    leader_player_id: str | None
    player_count: int
    min_players: int
    max_players: int
    status: str  # WAITING_FOR_LEADER | WAITING_FOR_PLAYERS | READY | FULL
    is_ready: bool
    team_code: str | None  # only the host and the team's own leader ever receive this
    members: list[str]  # only the host and the team's own members receive this


class LobbyPlayerOut(BaseModel):
    player_id: str | None  # host only (needed for leader assignment and kicks)
    display_name: str
    pawn_color: str
    team_id: str | None
    team_name: str | None
    role: str  # LEADER | PLAYER | UNASSIGNED
    is_connected: bool


class ChatMessageOut(BaseModel):
    display_name: str
    body: str
    created_at: datetime


class ChatOut(BaseModel):
    enabled: bool
    closed_message: str | None
    messages: list[ChatMessageOut]


class ReadinessOut(BaseModel):
    requirements_met: bool
    ready_to_start: bool
    problems: list[ProblemOut]


class LobbyCountsOut(BaseModel):
    player_count: int
    unassigned_count: int
    capacity: int
    min_total_players: int
    max_total_players: int


class LobbyConfigOut(BaseModel):
    team_count: int
    min_players_per_team: int
    max_players_per_team: int


class YouOut(BaseModel):
    role: str  # host | player
    player_id: str | None = None
    display_name: str | None = None
    team_id: str | None = None
    team_name: str | None = None
    is_leader: bool = False
    team_code: str | None = None


class HostActionsOut(BaseModel):
    can_start_formation: bool
    can_lock: bool
    can_reopen: bool
    can_start_game: bool  # reflects server readiness; the start endpoint itself arrives later


class LobbyResponse(BaseModel):
    game_code: str
    lobby_state: str  # WAITING | TEAM_FORMATION | READY_TO_START
    game_status: str
    team_formation_locked: bool
    config: LobbyConfigOut
    counts: LobbyCountsOut
    teams: list[LobbyTeamOut]
    players: list[LobbyPlayerOut]
    chat: ChatOut
    readiness: ReadinessOut
    you: YouOut
    host_actions: HostActionsOut | None = None
