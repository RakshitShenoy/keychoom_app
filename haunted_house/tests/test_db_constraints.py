"""The database itself must reject invalid or duplicate rows (not just the app code)."""
import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    DareConfirmation,
    Game,
    HintUsage,
    ObjectState,
    Player,
    Team,
    TeamFloorState,
)
from app.timers.clock import utcnow


def make_game(db, code="ABC234", token_hash="h1"):
    game = Game(
        game_code=code,
        status="LOBBY",
        host_token_hash=token_hash,
        host_session_expires_at=utcnow(),
        min_teams=3,
        max_teams=5,
        min_players_per_team=3,
        max_players_per_team=5,
        base_duration_seconds=3600,
        team_count=3,
    )
    db.add(game)
    db.commit()
    return game


def make_team(db, game, name="Ghosts", code="TEAM01"):
    slot_no = db.query(Team).filter(Team.game_id == game.id).count() + 1
    team = Team(game_id=game.id, slot_no=slot_no, name=name, team_code=code)
    db.add(team)
    db.commit()
    return team


def test_game_code_unique(db):
    make_game(db)
    with pytest.raises(IntegrityError):
        make_game(db, token_hash="h2")


def test_team_code_unique(db):
    game = make_game(db)
    make_team(db, game, "A", "CODE01")
    with pytest.raises(IntegrityError):
        make_team(db, game, "B", "CODE01")


def test_teams_without_code_can_coexist(db):
    game = make_game(db)
    db.add_all(
        [Team(game_id=game.id, slot_no=1, name="A"), Team(game_id=game.id, slot_no=2, name="B")]
    )
    db.commit()


def test_team_name_unique_per_game(db):
    game = make_game(db)
    make_team(db, game, "Ghosts", "C1")
    with pytest.raises(IntegrityError):
        make_team(db, game, "Ghosts", "C2")


def test_player_belongs_to_a_team_and_name_unique(db):
    game = make_game(db)
    team = make_team(db, game)
    kwargs = dict(
        game_id=game.id, team_id=team.id, display_name="Al", display_name_key="al",
        session_token_hash="t1", session_expires_at=utcnow(), pawn_color="#ffffff",
    )
    db.add(Player(**kwargs))
    db.commit()
    db.add(Player(**{**kwargs, "display_name": "AL", "session_token_hash": "t2"}))
    with pytest.raises(IntegrityError):
        db.commit()


def test_player_team_must_exist(db):
    game = make_game(db)
    db.add(
        Player(
            game_id=game.id, team_id="does-not-exist", display_name="Al", display_name_key="al",
            session_token_hash="t1", session_expires_at=utcnow(), pawn_color="#ffffff",
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()


def test_one_floor_state_per_team_and_floor(db):
    team = make_team(db, make_game(db))
    db.add(TeamFloorState(team_id=team.id, floor_no=1, preset_id="f1a"))
    db.commit()
    db.add(TeamFloorState(team_id=team.id, floor_no=1, preset_id="f1b"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_floor_number_range_enforced(db):
    team = make_team(db, make_game(db))
    db.add(TeamFloorState(team_id=team.id, floor_no=9, preset_id="x"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_one_object_state_per_team_floor_object(db):
    team = make_team(db, make_game(db))
    db.add(ObjectState(team_id=team.id, floor_no=3, object_id="candle"))
    db.commit()
    db.add(ObjectState(team_id=team.id, floor_no=3, object_id="candle"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_one_hint_record_per_team_floor_hint_number(db):
    team = make_team(db, make_game(db))
    db.add(HintUsage(team_id=team.id, floor_no=2, hint_number=1, penalty_seconds=30))
    db.commit()
    db.add(HintUsage(team_id=team.id, floor_no=2, hint_number=1, penalty_seconds=30))
    with pytest.raises(IntegrityError):
        db.commit()


def test_hint_number_range_enforced(db):
    team = make_team(db, make_game(db))
    db.add(HintUsage(team_id=team.id, floor_no=2, hint_number=5, penalty_seconds=30))
    with pytest.raises(IntegrityError):
        db.commit()


def test_one_dare_confirmation_per_team_floor_dare(db):
    team = make_team(db, make_game(db))
    db.add(DareConfirmation(team_id=team.id, floor_no=1, dare_id="wave"))
    db.commit()
    db.add(DareConfirmation(team_id=team.id, floor_no=1, dare_id="wave"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_invalid_status_rejected(db):
    game = make_game(db)
    game.status = "BOGUS"
    with pytest.raises(IntegrityError):
        db.commit()


def test_deleting_a_game_cascades(db):
    game = make_game(db)
    team = make_team(db, game)
    db.add(ObjectState(team_id=team.id, floor_no=1, object_id="door"))
    db.commit()
    db.delete(game)
    db.commit()
    assert db.query(Team).count() == 0
    assert db.query(ObjectState).count() == 0
