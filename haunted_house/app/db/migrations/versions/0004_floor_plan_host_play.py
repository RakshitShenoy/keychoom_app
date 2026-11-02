"""Phase 13: floor count, hidden floor order, host plays

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-31

Additive only. Existing games keep every row: they become 8-floor games in the original order
(1..8) and "host only".
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("games") as batch_op:
        batch_op.add_column(sa.Column("floor_count", sa.Integer(), nullable=False, server_default="8"))
        batch_op.add_column(
            sa.Column("floor_order", sa.JSON(), nullable=False, server_default=sa.text("'[1,2,3,4,5,6,7,8]'"))
        )
        batch_op.add_column(sa.Column("host_plays", sa.Boolean(), nullable=False, server_default="0"))
        batch_op.create_check_constraint(
            op.f("ck_games_floor_count_range"), "floor_count >= 3 AND floor_count <= 8"
        )
    with op.batch_alter_table("players") as batch_op:
        batch_op.add_column(sa.Column("is_host_player", sa.Boolean(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("players") as batch_op:
        batch_op.drop_column("is_host_player")
    with op.batch_alter_table("games") as batch_op:
        batch_op.drop_constraint(op.f("ck_games_floor_count_range"), type_="check")
        batch_op.drop_column("host_plays")
        batch_op.drop_column("floor_order")
        batch_op.drop_column("floor_count")
