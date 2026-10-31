"""Builds the lobby payload. Everything the UI shows (statuses, readiness, which buttons are
enabled) is calculated here on the server; the browser never decides any of it."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.db.enums import GameStatus
from app.db.models import Game, Player
from app.lobby.chat import CHAT_CLOSED_MESSAGE, chat_is_open, recent_messages
from app.lobby.formation import lobby_state_name
from app.schemas.lobby import (
    ChatMessageOut, ChatOut, HostActionsOut, LobbyConfigOut, LobbyCountsOut, LobbyPlayerOut,
    LobbyResponse, LobbyTeamOut, ProblemOut, ReadinessOut, YouOut,
)
from app.teams.queries import LobbyData, load_lobby_data
from app.teams.readiness import team_is_ready, team_status

if TYPE_CHECKING:
    from app.config import Settings


@dataclass
class Viewer:
    role: str  # "host" | "player"
    game: Game
    player: Player | None = None


def build_lobby_view(
    db: Session,
    settings: "Settings",
    viewer: Viewer,
    *,
    data: LobbyData | None = None,
    messages: list | None = None,
) -> LobbyResponse:
    """``data`` / ``messages`` may be passed in so one database load can serve many viewers."""
    game = viewer.game
    is_host = viewer.role == "host"
    me = viewer.player
    data = data if data is not None else load_lobby_data(db, game)
    rules = data.rules
    team_names = {t.id: (t.name or f"Team {t.slot_no}") for t in data.teams}
    leader_by_team = {t.id: t.leader_player_id for t in data.teams}
    players_by_id = {p.id: p for p in data.players}

    teams_out: list[LobbyTeamOut] = []
    for team in data.teams:
        snap = data.snapshots[team.id]
        is_my_team_leader = me is not None and team.leader_player_id == me.id
        is_my_team = me is not None and me.team_id == team.id
        leader = players_by_id.get(team.leader_player_id) if team.leader_player_id else None
        teams_out.append(
            LobbyTeamOut(
                team_id=team.id,
                slot_no=team.slot_no,
                name=team_names[team.id],
                icon=team.icon,
                leader_name=leader.display_name if leader else None,
                leader_player_id=team.leader_player_id if is_host else None,
                player_count=snap.player_count,
                min_players=rules.min_players,
                max_players=rules.max_players,
                status=team_status(snap, rules).value,
                is_ready=team_is_ready(snap, rules),
                team_code=team.team_code if (is_host or is_my_team_leader) else None,
                members=[p.display_name for p in data.members[team.id]] if (is_host or is_my_team) else [],
            )
        )

    players_out: list[LobbyPlayerOut] = []
    for p in data.players:
        if leader_by_team.get(p.team_id) == p.id:
            role = "LEADER"
        elif p.team_id in team_names:
            role = "PLAYER"
        else:
            role = "UNASSIGNED"
        players_out.append(
            LobbyPlayerOut(
                player_id=p.id if is_host else None,
                display_name=p.display_name,
                pawn_color=p.pawn_color,
                team_id=p.team_id if is_host else None,
                team_name=team_names.get(p.team_id),
                role=role,
                is_connected=p.is_connected,
            )
        )

    open_chat = chat_is_open(game)
    chat = ChatOut(
        enabled=open_chat,
        closed_message=None if open_chat else CHAT_CLOSED_MESSAGE,
        messages=[
            ChatMessageOut(display_name=m.display_name, body=m.body, created_at=m.created_at)
            for m in (messages if messages is not None else recent_messages(db, game, settings.chat_history_limit))
        ],
    )

    locked = game.status == GameStatus.READY.value
    ready_to_start = locked and data.readiness.requirements_met
    readiness = ReadinessOut(
        requirements_met=data.readiness.requirements_met,
        ready_to_start=ready_to_start,
        problems=[ProblemOut(code=p.code, message=p.message, team_id=p.team_id) for p in data.readiness.problems],
    )

    if is_host:
        you = YouOut(role="host")
        actions = HostActionsOut(
            can_start_formation=game.status == GameStatus.LOBBY.value,
            can_lock=game.status == GameStatus.TEAM_FORMATION.value and data.readiness.requirements_met,
            can_reopen=locked,
            can_start_game=ready_to_start,
        )
    else:
        assert me is not None
        my_team = next((t for t in data.teams if t.id == me.team_id), None)
        is_leader = my_team is not None and my_team.leader_player_id == me.id
        you = YouOut(
            role="player",
            player_id=me.id,
            display_name=me.display_name,
            team_id=me.team_id,
            team_name=team_names.get(me.team_id),
            is_leader=is_leader,
            team_code=my_team.team_code if is_leader and my_team else None,
        )
        actions = None

    db.commit()  # reads are done: hand the connection back now (see app/db/session.py)
    return LobbyResponse(
        game_code=game.game_code,
        lobby_state=lobby_state_name(game),
        game_status=game.status,
        team_formation_locked=locked,
        config=LobbyConfigOut(
            team_count=rules.team_count,
            min_players_per_team=rules.min_players,
            max_players_per_team=rules.max_players,
        ),
        counts=LobbyCountsOut(
            player_count=len(data.players),
            unassigned_count=len(data.unassigned),
            capacity=rules.team_count * rules.max_players,
            min_total_players=rules.team_count * rules.min_players,
            max_total_players=rules.team_count * rules.max_players,
        ),
        teams=teams_out,
        players=players_out,
        chat=chat,
        readiness=readiness,
        you=you,
        host_actions=actions,
    )
