"""Loads everything the lobby rules and views need, in a fixed number of queries."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Game, Player, Team
from app.teams.readiness import Readiness, TeamRules, TeamSnapshot, evaluate_readiness


@dataclass
class LobbyData:
    rules: TeamRules
    teams: list[Team]
    players: list[Player]
    members: dict[str, list[Player]]
    snapshots: dict[str, TeamSnapshot]
    unassigned: list[Player]
    readiness: Readiness


def rules_for(game: Game) -> TeamRules:
    return TeamRules(
        team_count=game.team_count,
        min_players=game.min_players_per_team,
        max_players=game.max_players_per_team,
    )


def load_lobby_data(db: Session, game: Game) -> LobbyData:
    # populate_existing: never trust objects cached earlier in this session.
    teams = list(
        db.scalars(
            select(Team)
            .where(Team.game_id == game.id)
            .order_by(Team.slot_no)
            .execution_options(populate_existing=True)
        ).all()
    )
    players = list(
        db.scalars(
            select(Player)
            .where(Player.game_id == game.id)
            .order_by(Player.joined_at, Player.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    members: dict[str, list[Player]] = {t.id: [] for t in teams}
    unassigned: list[Player] = []
    for player in players:
        if player.team_id in members:
            members[player.team_id].append(player)
        else:
            unassigned.append(player)

    rules = rules_for(game)
    snapshots = {
        t.id: TeamSnapshot(
            team_id=t.id,
            name=t.name or f"Team {t.slot_no}",
            has_leader=t.leader_player_id is not None,
            player_count=len(members[t.id]),
        )
        for t in teams
    }
    readiness = evaluate_readiness(list(snapshots.values()), len(unassigned), rules)
    return LobbyData(rules, teams, players, members, snapshots, unassigned, readiness)
