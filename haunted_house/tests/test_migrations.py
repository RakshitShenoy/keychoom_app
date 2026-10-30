from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

import app.db.models  # noqa: F401
from app.db.base import Base

ROOT = Path(__file__).resolve().parent.parent
EXPECTED_TABLES = {
    "games", "players", "teams", "team_floor_state", "object_state", "hint_usage",
    "quiz_attempts", "dare_confirmations", "jumpscare_events", "chat_messages", "game_events",
}


def alembic_config(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "app" / "db" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def test_upgrade_creates_all_tables_and_downgrade_removes_them(tmp_path):
    url = f"sqlite:///{tmp_path / 'migrate.db'}"
    cfg = alembic_config(url)

    command.upgrade(cfg, "head")
    engine = create_engine(url)
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())

    command.downgrade(cfg, "base")
    assert EXPECTED_TABLES.isdisjoint(set(inspect(engine).get_table_names()))
    engine.dispose()


def test_migration_matches_the_models(tmp_path):
    """Guards against the models and the hand-written migration drifting apart."""
    url = f"sqlite:///{tmp_path / 'drift.db'}"
    command.upgrade(alembic_config(url), "head")
    engine = create_engine(url)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        differences = compare_metadata(context, Base.metadata)
    engine.dispose()
    assert differences == []


def test_phase2_migration_backfills_team_count_and_round_trips(tmp_path):
    """Upgrading a Phase 1 database keeps its games and gives them team_count = max_teams."""
    from sqlalchemy import text

    url = f"sqlite:///{tmp_path / 'phase1.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0001")

    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO games (id, game_code, status, host_token_hash, host_session_expires_at,"
                " min_teams, max_teams, min_players_per_team, max_players_per_team,"
                " base_duration_seconds, created_at)"
                " VALUES ('g1', 'ABC234', 'LOBBY', 'h', '2026-01-01 00:00:00', 3, 7, 3, 5, 3600,"
                " '2026-01-01 00:00:00')"
            )
        )

    command.upgrade(cfg, "head")
    with engine.connect() as connection:
        row = connection.execute(text("SELECT team_count, max_teams FROM games")).one()
        assert row.team_count == row.max_teams == 7
        columns = {c["name"] for c in inspect(connection).get_columns("teams")}
        assert {"slot_no", "leader_player_id"} <= columns

    command.downgrade(cfg, "0001")  # and back again
    command.upgrade(cfg, "head")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM games")).scalar() == 1
    engine.dispose()


def test_phase10_migration_is_additive_and_round_trips(tmp_path):
    """0003 adds the pause columns without touching existing games (paused_seconds_total backfills to 0)."""
    from sqlalchemy import text

    url = f"sqlite:///{tmp_path / 'phase10.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0002")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO games (id, game_code, status, host_token_hash, host_session_expires_at,"
                " min_teams, max_teams, min_players_per_team, max_players_per_team,"
                " base_duration_seconds, team_count, created_at)"
                " VALUES ('g1', 'ABC234', 'RUNNING', 'h', '2026-01-01 00:00:00', 3, 7, 3, 5, 3600, 4,"
                " '2026-01-01 00:00:00')"
            )
        )
    command.upgrade(cfg, "head")
    with engine.connect() as connection:
        row = connection.execute(text("SELECT status, paused_at, paused_seconds_total FROM games")).one()
        assert (row.status, row.paused_at, row.paused_seconds_total) == ("RUNNING", None, 0)
    command.downgrade(cfg, "0002")
    command.upgrade(cfg, "head")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM games")).scalar() == 1
    engine.dispose()
