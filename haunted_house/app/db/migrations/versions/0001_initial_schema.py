"""Initial schema (Phase 1 foundation)

Revision ID: 0001
Revises:
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- games ---------------------------------------------------------------
    op.create_table(
        "games",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("game_code", sa.String(length=12), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("host_token_hash", sa.String(length=64), nullable=False),
        sa.Column("host_session_expires_at", sa.DateTime(), nullable=False),
        sa.Column("min_teams", sa.Integer(), nullable=False),
        sa.Column("max_teams", sa.Integer(), nullable=False),
        sa.Column("min_players_per_team", sa.Integer(), nullable=False),
        sa.Column("max_players_per_team", sa.Integer(), nullable=False),
        sa.Column("base_duration_seconds", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("winner_team_id", sa.String(length=32), nullable=True),
        sa.CheckConstraint(
            "status IN ('LOBBY', 'TEAM_FORMATION', 'READY', 'CUTSCENE', 'RUNNING', 'FINISHED')",
            name=op.f("ck_games_status"),
        ),
        sa.CheckConstraint(
            "min_teams >= 1 AND max_teams >= min_teams", name=op.f("ck_games_team_bounds")
        ),
        sa.CheckConstraint(
            "min_players_per_team >= 1 AND max_players_per_team >= min_players_per_team",
            name=op.f("ck_games_player_bounds"),
        ),
        sa.CheckConstraint(
            "base_duration_seconds > 0", name=op.f("ck_games_base_duration_positive")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_games")),
    )
    op.create_index(op.f("ix_games_game_code"), "games", ["game_code"], unique=True)
    op.create_index(op.f("ix_games_host_token_hash"), "games", ["host_token_hash"], unique=True)

    # --- teams ---------------------------------------------------------------
    op.create_table(
        "teams",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("game_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=True),
        sa.Column("icon", sa.String(length=32), nullable=True),
        sa.Column("leader_player_id", sa.String(length=32), nullable=True),
        sa.Column("team_code", sa.String(length=12), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("current_floor", sa.Integer(), nullable=False),
        sa.Column("floors_completed", sa.Integer(), nullable=False),
        sa.Column("hint_penalty_seconds", sa.Integer(), nullable=False),
        sa.Column("deadline_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('FORMING', 'LOCKED', 'PLAYING', 'ESCAPED', 'ELIMINATED')",
            name=op.f("ck_teams_status"),
        ),
        sa.CheckConstraint(
            "current_floor >= 1 AND current_floor <= 8", name=op.f("ck_teams_current_floor_range")
        ),
        sa.CheckConstraint(
            "floors_completed >= 0 AND floors_completed <= 8",
            name=op.f("ck_teams_floors_completed_range"),
        ),
        sa.CheckConstraint(
            "hint_penalty_seconds >= 0", name=op.f("ck_teams_hint_penalty_nonnegative")
        ),
        sa.ForeignKeyConstraint(
            ["game_id"], ["games.id"], name=op.f("fk_teams_game_id_games"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_teams")),
        sa.UniqueConstraint("game_id", "name", name=op.f("uq_teams_game_id_name")),
    )
    op.create_index(op.f("ix_teams_team_code"), "teams", ["team_code"], unique=True)

    # --- players -------------------------------------------------------------
    op.create_table(
        "players",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("game_id", sa.String(length=32), nullable=False),
        sa.Column("team_id", sa.String(length=32), nullable=True),
        sa.Column("display_name", sa.String(length=32), nullable=False),
        sa.Column("display_name_key", sa.String(length=32), nullable=False),
        sa.Column("session_token_hash", sa.String(length=64), nullable=False),
        sa.Column("session_expires_at", sa.DateTime(), nullable=False),
        sa.Column("pawn_color", sa.String(length=7), nullable=False),
        sa.Column("is_connected", sa.Boolean(), nullable=False),
        sa.Column("joined_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["game_id"], ["games.id"], name=op.f("fk_players_game_id_games"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name=op.f("fk_players_team_id_teams"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_players")),
        sa.UniqueConstraint(
            "game_id", "display_name_key", name=op.f("uq_players_game_id_display_name_key")
        ),
    )
    op.create_index(op.f("ix_players_team_id"), "players", ["team_id"], unique=False)
    op.create_index(
        op.f("ix_players_session_token_hash"), "players", ["session_token_hash"], unique=True
    )

    # --- team_floor_state ----------------------------------------------------
    op.create_table(
        "team_floor_state",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("team_id", sa.String(length=32), nullable=False),
        sa.Column("floor_no", sa.Integer(), nullable=False),
        sa.Column("preset_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("vault_pieces", sa.JSON(), nullable=False),
        sa.Column("fragment_awarded", sa.Boolean(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "floor_no >= 1 AND floor_no <= 8", name=op.f("ck_team_floor_state_floor_no_range")
        ),
        sa.CheckConstraint(
            "status IN ('LOCKED', 'ACTIVE', 'COMPLETED')", name=op.f("ck_team_floor_state_status")
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name=op.f("fk_team_floor_state_team_id_teams"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_team_floor_state")),
        sa.UniqueConstraint(
            "team_id", "floor_no", name=op.f("uq_team_floor_state_team_id_floor_no")
        ),
    )

    # --- object_state --------------------------------------------------------
    op.create_table(
        "object_state",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("team_id", sa.String(length=32), nullable=False),
        sa.Column("floor_no", sa.Integer(), nullable=False),
        sa.Column("object_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("solved_by_player_id", sa.String(length=32), nullable=True),
        sa.Column("solved_at", sa.DateTime(), nullable=True),
        sa.Column("progress", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "floor_no >= 1 AND floor_no <= 8", name=op.f("ck_object_state_floor_no_range")
        ),
        sa.CheckConstraint(
            "status IN ('UNTOUCHED', 'IN_PROGRESS', 'SOLVED')", name=op.f("ck_object_state_status")
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name=op.f("fk_object_state_team_id_teams"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["solved_by_player_id"],
            ["players.id"],
            name=op.f("fk_object_state_solved_by_player_id_players"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_object_state")),
        sa.UniqueConstraint(
            "team_id",
            "floor_no",
            "object_id",
            name=op.f("uq_object_state_team_id_floor_no_object_id"),
        ),
    )

    # --- hint_usage ----------------------------------------------------------
    op.create_table(
        "hint_usage",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("team_id", sa.String(length=32), nullable=False),
        sa.Column("floor_no", sa.Integer(), nullable=False),
        sa.Column("hint_number", sa.Integer(), nullable=False),
        sa.Column("object_id", sa.String(length=64), nullable=True),
        sa.Column("penalty_seconds", sa.Integer(), nullable=False),
        sa.Column("requested_by_player_id", sa.String(length=32), nullable=True),
        sa.Column("used_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "floor_no >= 1 AND floor_no <= 8", name=op.f("ck_hint_usage_floor_no_range")
        ),
        sa.CheckConstraint(
            "hint_number >= 1 AND hint_number <= 4", name=op.f("ck_hint_usage_hint_number_range")
        ),
        sa.CheckConstraint(
            "penalty_seconds >= 0", name=op.f("ck_hint_usage_penalty_nonnegative")
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name=op.f("fk_hint_usage_team_id_teams"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_player_id"],
            ["players.id"],
            name=op.f("fk_hint_usage_requested_by_player_id_players"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hint_usage")),
        sa.UniqueConstraint(
            "team_id",
            "floor_no",
            "hint_number",
            name=op.f("uq_hint_usage_team_id_floor_no_hint_number"),
        ),
    )

    # --- quiz_attempts -------------------------------------------------------
    op.create_table(
        "quiz_attempts",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("team_id", sa.String(length=32), nullable=False),
        sa.Column("floor_no", sa.Integer(), nullable=False),
        sa.Column("object_id", sa.String(length=64), nullable=False),
        sa.Column("question_ids", sa.JSON(), nullable=False),
        sa.Column("answers", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ends_at", sa.DateTime(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "floor_no >= 1 AND floor_no <= 8", name=op.f("ck_quiz_attempts_floor_no_range")
        ),
        sa.CheckConstraint(
            "score IS NULL OR score >= 0", name=op.f("ck_quiz_attempts_score_nonnegative")
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name=op.f("fk_quiz_attempts_team_id_teams"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quiz_attempts")),
    )
    op.create_index(
        op.f("ix_quiz_attempts_team_id_floor_no"),
        "quiz_attempts",
        ["team_id", "floor_no"],
        unique=False,
    )

    # --- dare_confirmations --------------------------------------------------
    op.create_table(
        "dare_confirmations",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("team_id", sa.String(length=32), nullable=False),
        sa.Column("floor_no", sa.Integer(), nullable=False),
        sa.Column("dare_id", sa.String(length=64), nullable=False),
        sa.Column("confirmed_by_player_id", sa.String(length=32), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "floor_no >= 1 AND floor_no <= 8", name=op.f("ck_dare_confirmations_floor_no_range")
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name=op.f("fk_dare_confirmations_team_id_teams"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by_player_id"],
            ["players.id"],
            name=op.f("fk_dare_confirmations_confirmed_by_player_id_players"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dare_confirmations")),
        sa.UniqueConstraint(
            "team_id",
            "floor_no",
            "dare_id",
            name=op.f("uq_dare_confirmations_team_id_floor_no_dare_id"),
        ),
    )

    # --- jumpscare_events ----------------------------------------------------
    op.create_table(
        "jumpscare_events",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("team_id", sa.String(length=32), nullable=False),
        sa.Column("target_player_id", sa.String(length=32), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("asset_id", sa.String(length=64), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(), nullable=False),
        sa.Column("fired_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name=op.f("fk_jumpscare_events_team_id_teams"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_player_id"],
            ["players.id"],
            name=op.f("fk_jumpscare_events_target_player_id_players"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jumpscare_events")),
    )
    op.create_index(
        op.f("ix_jumpscare_events_team_id"), "jumpscare_events", ["team_id"], unique=False
    )

    # --- chat_messages -------------------------------------------------------
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("game_id", sa.String(length=32), nullable=False),
        sa.Column("player_id", sa.String(length=32), nullable=True),
        sa.Column("display_name", sa.String(length=32), nullable=False),
        sa.Column("body", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["game_id"],
            ["games.id"],
            name=op.f("fk_chat_messages_game_id_games"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["players.id"],
            name=op.f("fk_chat_messages_player_id_players"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_messages")),
    )
    op.create_index(
        op.f("ix_chat_messages_game_id_created_at"),
        "chat_messages",
        ["game_id", "created_at"],
        unique=False,
    )

    # --- game_events ---------------------------------------------------------
    op.create_table(
        "game_events",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("game_id", sa.String(length=32), nullable=False),
        sa.Column("team_id", sa.String(length=32), nullable=True),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["game_id"], ["games.id"], name=op.f("fk_game_events_game_id_games"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name=op.f("fk_game_events_team_id_teams"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_game_events")),
    )
    op.create_index(
        op.f("ix_game_events_game_id_created_at"),
        "game_events",
        ["game_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    # Children first, parents last.
    op.drop_table("game_events")
    op.drop_table("chat_messages")
    op.drop_table("jumpscare_events")
    op.drop_table("dare_confirmations")
    op.drop_table("quiz_attempts")
    op.drop_table("hint_usage")
    op.drop_table("object_state")
    op.drop_table("team_floor_state")
    op.drop_table("players")
    op.drop_table("teams")
    op.drop_table("games")
