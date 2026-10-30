import pytest
from sqlalchemy import func, select

from app.db.models import Game, Team
from app.teams.slots import ensure_team_slots


def test_valid_team_count_is_stored_and_reported(make_lobby):
    lobby = make_lobby(team_count=4, min_players=3, max_players=5)
    assert lobby.game["team_count"] == 4
    assert lobby.game["config"]["min_players_per_team"] == 3
    assert lobby.lobby()["config"] == {
        "team_count": 4, "min_players_per_team": 3, "max_players_per_team": 5
    }


@pytest.mark.parametrize("count", [0, 2, 26, 100])
def test_team_count_outside_global_limits_rejected(make_game, count):
    response = make_game(team_count=count)
    assert response.status_code == 422
    assert "team_count" in [d["field"] for d in response.json()["error"]["details"]]


@pytest.mark.parametrize(
    "overrides,field",
    [
        ({"min_players_per_team": 2}, "min_players_per_team"),
        ({"max_players_per_team": 11}, "max_players_per_team"),
        ({"min_players_per_team": 6, "max_players_per_team": 4}, "max_players_per_team"),
        ({"team_count": 3, "min_teams": 4, "max_teams": 6}, "team_count"),
        ({"team_count": 9, "min_teams": 3, "max_teams": 6}, "team_count"),
    ],
)
def test_invalid_player_and_team_ranges(make_game, overrides, field):
    response = make_game(**overrides)
    assert response.status_code == 422
    assert field in [d["field"] for d in response.json()["error"]["details"]]


def test_team_count_defaults_to_max_teams(make_game):
    assert make_game(min_teams=3, max_teams=7).json()["team_count"] == 7


def test_team_count_must_be_an_integer(make_game):
    assert make_game(team_count="4").status_code == 422
    assert make_game(team_count=3.5).status_code == 422


def test_exactly_team_count_slots_are_created(make_lobby, db):
    lobby = make_lobby(team_count=4)
    teams = lobby.lobby()["teams"]
    assert [t["slot_no"] for t in teams] == [1, 2, 3, 4]
    assert len({t["name"] for t in teams}) == 4  # distinct names
    assert all(t["leader_name"] is None and t["team_code"] is None for t in teams)
    assert all(t["status"] == "WAITING_FOR_LEADER" for t in teams)


def test_teams_belong_to_their_own_game(make_lobby, db):
    first = make_lobby(team_count=3)
    second = make_lobby(team_count=5)
    count_first = db.scalar(select(func.count()).select_from(Team).where(Team.game_id == first.game["game_id"]))
    count_second = db.scalar(select(func.count()).select_from(Team).where(Team.game_id == second.game["game_id"]))
    assert (count_first, count_second) == (3, 5)


def test_ensure_team_slots_is_idempotent(make_lobby, db):
    lobby = make_lobby(team_count=4)
    game = db.get(Game, lobby.game["game_id"])
    for _ in range(3):  # a refresh / retry must never create duplicates
        ensure_team_slots(db, game)
        db.commit()
    assert db.scalar(select(func.count()).select_from(Team).where(Team.game_id == game.id)) == 4


def test_repeated_start_does_not_duplicate_teams(make_lobby):
    lobby = make_lobby(team_count=4)
    lobby.start_formation()
    assert lobby.host.post(lobby.url("/team-formation/start")).status_code == 409
    assert len(lobby.lobby()["teams"]) == 4


def test_missing_slots_are_repaired_when_formation_starts(make_lobby, db):
    lobby = make_lobby(team_count=3)
    db.query(Team).filter(Team.game_id == lobby.game["game_id"], Team.slot_no == 3).delete()
    db.commit()
    assert len(lobby.lobby()["teams"]) == 2
    lobby.start_formation()
    assert len(lobby.lobby()["teams"]) == 3


def test_team_codes_do_not_exist_until_a_leader_is_approved(make_lobby, db):
    lobby = make_lobby(team_count=3)
    codes = db.scalars(select(Team.team_code).where(Team.game_id == lobby.game["game_id"])).all()
    assert codes == [None, None, None]
