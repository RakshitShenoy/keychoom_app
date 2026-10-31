import pytest

from app.teams.readiness import (
    TeamReadiness, TeamRules, TeamSnapshot, evaluate_readiness, team_is_ready, team_status,
)

RULES = TeamRules(team_count=3, min_players=3, max_players=5)


def snap(name, leader, count):
    return TeamSnapshot(team_id=name, name=name, has_leader=leader, player_count=count)


@pytest.mark.parametrize(
    "leader,count,expected,ready",
    [
        (False, 0, TeamReadiness.WAITING_FOR_LEADER, False),
        (False, 4, TeamReadiness.WAITING_FOR_LEADER, False),
        (True, 1, TeamReadiness.WAITING_FOR_PLAYERS, False),
        (True, 2, TeamReadiness.WAITING_FOR_PLAYERS, False),
        (True, 3, TeamReadiness.READY, True),
        (True, 4, TeamReadiness.READY, True),
        (True, 5, TeamReadiness.FULL, True),
        (True, 6, TeamReadiness.FULL, False),  # over capacity can never be ready
    ],
)
def test_team_status_and_readiness(leader, count, expected, ready):
    team = snap("T", leader, count)
    assert team_status(team, RULES) == expected
    assert team_is_ready(team, RULES) is ready


def test_game_ready_when_every_team_ready_and_nobody_unassigned():
    teams = [snap("A", True, 3), snap("B", True, 5), snap("C", True, 4)]
    result = evaluate_readiness(teams, 0, RULES)
    assert result.requirements_met and result.problems == ()


def test_each_problem_is_reported():
    teams = [snap("A", False, 3), snap("B", True, 2), snap("C", True, 6)]
    codes = {p.code for p in evaluate_readiness(teams, 2, RULES).problems}
    assert codes == {"TEAM_NO_LEADER", "TEAM_TOO_SMALL", "TEAM_TOO_LARGE", "UNASSIGNED_PLAYERS"}


def test_missing_team_slot_is_a_problem():
    teams = [snap("A", True, 3), snap("B", True, 3)]
    codes = [p.code for p in evaluate_readiness(teams, 0, RULES).problems]
    assert codes == ["WRONG_TEAM_COUNT"]
