"""The database itself guards the Phase 2 invariants."""
import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import Game, Player, Team
from app.timers.clock import utcnow


def make_game(db, **overrides):
    values = dict(
        game_code="ABC234", status="LOBBY", host_token_hash="h1", host_session_expires_at=utcnow(),
        min_teams=3, max_teams=5, min_players_per_team=3, max_players_per_team=5,
        base_duration_seconds=3600, team_count=4,
    )
    values.update(overrides)
    game = Game(**values)
    db.add(game)
    db.commit()
    return game


def test_team_count_must_be_inside_min_and_max_teams(db):
    with pytest.raises(IntegrityError):
        make_game(db, team_count=2)
    db.rollback()
    with pytest.raises(IntegrityError):
        make_game(db, game_code="ABC235", host_token_hash="h2", team_count=6)


def test_slot_numbers_are_unique_per_game_and_positive(db):
    game = make_game(db)
    db.add(Team(game_id=game.id, slot_no=1, name="A"))
    db.commit()
    db.add(Team(game_id=game.id, slot_no=1, name="B"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    db.add(Team(game_id=game.id, slot_no=0, name="C"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_leader_must_be_a_real_player(db):
    game = make_game(db)
    db.add(Team(game_id=game.id, slot_no=1, name="A", leader_player_id="nobody"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_deleting_a_leader_clears_the_leader_reference(db):
    game = make_game(db)
    team = Team(game_id=game.id, slot_no=1, name="A")
    db.add(team)
    db.commit()
    player = Player(
        game_id=game.id, team_id=team.id, display_name="Al", display_name_key="al",
        session_token_hash="t1", session_expires_at=utcnow(), pawn_color="#ffffff",
    )
    db.add(player)
    db.commit()
    team.leader_player_id = player.id
    db.commit()
    db.delete(player)
    db.commit()
    db.refresh(team)
    assert team.leader_player_id is None
