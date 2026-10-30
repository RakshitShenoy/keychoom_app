"""Server-side readiness rules. Pure functions: no database, no HTTP, easy to test.

A team is READY only when it has a leader AND at least ``min_players`` members.
It can never exceed ``max_players``. The game may lock team formation (and later start) only when
every required team exists and is ready and nobody is left without a team.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence


class TeamReadiness(str, Enum):
    WAITING_FOR_LEADER = "WAITING_FOR_LEADER"
    WAITING_FOR_PLAYERS = "WAITING_FOR_PLAYERS"
    READY = "READY"
    FULL = "FULL"


@dataclass(frozen=True)
class TeamRules:
    team_count: int
    min_players: int
    max_players: int


@dataclass(frozen=True)
class TeamSnapshot:
    team_id: str
    name: str
    has_leader: bool
    player_count: int


@dataclass(frozen=True)
class Problem:
    code: str
    message: str
    team_id: str | None = None


@dataclass(frozen=True)
class Readiness:
    requirements_met: bool
    problems: tuple[Problem, ...]


def team_status(team: TeamSnapshot, rules: TeamRules) -> TeamReadiness:
    if not team.has_leader:
        return TeamReadiness.WAITING_FOR_LEADER
    if team.player_count >= rules.max_players:
        return TeamReadiness.FULL
    if team.player_count >= rules.min_players:
        return TeamReadiness.READY
    return TeamReadiness.WAITING_FOR_PLAYERS


def team_is_ready(team: TeamSnapshot, rules: TeamRules) -> bool:
    return team.has_leader and rules.min_players <= team.player_count <= rules.max_players


def evaluate_readiness(
    teams: Sequence[TeamSnapshot], unassigned_players: int, rules: TeamRules
) -> Readiness:
    problems: list[Problem] = []

    if len(teams) != rules.team_count:
        problems.append(
            Problem(
                "WRONG_TEAM_COUNT",
                f"Expected {rules.team_count} teams but found {len(teams)}.",
            )
        )
    for team in teams:
        if not team.has_leader:
            problems.append(Problem("TEAM_NO_LEADER", f"{team.name} has no leader.", team.team_id))
        if team.player_count < rules.min_players:
            problems.append(
                Problem(
                    "TEAM_TOO_SMALL",
                    f"{team.name} has {team.player_count} players; at least {rules.min_players} needed.",
                    team.team_id,
                )
            )
        if team.player_count > rules.max_players:
            problems.append(
                Problem(
                    "TEAM_TOO_LARGE",
                    f"{team.name} has {team.player_count} players; at most {rules.max_players} allowed.",
                    team.team_id,
                )
            )
    if unassigned_players > 0:
        problems.append(
            Problem(
                "UNASSIGNED_PLAYERS",
                f"{unassigned_players} player(s) are not on a team yet.",
            )
        )
    return Readiness(requirements_met=not problems, problems=tuple(problems))
