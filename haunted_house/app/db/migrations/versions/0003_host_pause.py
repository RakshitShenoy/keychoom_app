"""Phase 10: host pause (games.paused_at, games.paused_seconds_total)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-29

Additive only: existing games keep all their data (paused_seconds_total backfills to 0).
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("games") as batch_op:
        batch_op.add_column(sa.Column("paused_at", sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column("paused_seconds_total", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.create_check_constraint(
            op.f("ck_games_paused_total_nonnegative"), "paused_seconds_total >= 0"
        )


def downgrade() -> None:
    with op.batch_alter_table("games") as batch_op:
        batch_op.drop_constraint(op.f("ck_games_paused_total_nonnegative"), type_="check")
        batch_op.drop_column("paused_seconds_total")
        batch_op.drop_column("paused_at")
