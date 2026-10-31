"""Phase 2: team formation (team_count, slots, leader foreign key)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-27

Uses batch mode so the same migration works on SQLite (table rebuild) and PostgreSQL (ALTER).
Phase 1 never created teams, so adding teams.slot_no cannot collide with existing rows.
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- games ---------------------------------------------------------------
    with op.batch_alter_table("games") as batch_op:
        batch_op.add_column(sa.Column("team_count", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("team_formation_started_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("team_formation_locked_at", sa.DateTime(), nullable=True))

    # Existing Phase 1 games had no explicit team count: use their configured maximum.
    op.execute("UPDATE games SET team_count = max_teams")

    with op.batch_alter_table("games") as batch_op:
        batch_op.alter_column("team_count", existing_type=sa.Integer(), nullable=False)
        batch_op.create_check_constraint(
            op.f("ck_games_team_count_bounds"),
            "team_count >= min_teams AND team_count <= max_teams",
        )

    # --- teams ---------------------------------------------------------------
    with op.batch_alter_table("teams") as batch_op:
        batch_op.add_column(
            sa.Column("slot_no", sa.Integer(), nullable=False, server_default=sa.text("1"))
        )
        batch_op.create_unique_constraint(op.f("uq_teams_game_id_slot_no"), ["game_id", "slot_no"])
        batch_op.create_check_constraint(op.f("ck_teams_slot_no_positive"), "slot_no >= 1")
        batch_op.create_foreign_key(
            op.f("fk_teams_leader_player_id_players"),
            "players",
            ["leader_player_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("teams") as batch_op:
        batch_op.drop_constraint(op.f("fk_teams_leader_player_id_players"), type_="foreignkey")
        batch_op.drop_constraint(op.f("ck_teams_slot_no_positive"), type_="check")
        batch_op.drop_constraint(op.f("uq_teams_game_id_slot_no"), type_="unique")
        batch_op.drop_column("slot_no")

    with op.batch_alter_table("games") as batch_op:
        batch_op.drop_constraint(op.f("ck_games_team_count_bounds"), type_="check")
        batch_op.drop_column("team_formation_locked_at")
        batch_op.drop_column("team_formation_started_at")
        batch_op.drop_column("team_count")
