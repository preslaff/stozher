"""bench: periods when a node was on the bench (its records are not evidence)

Revision ID: 0015
Revises: 0014
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"


def upgrade() -> None:
    op.create_table(
        "node_bench_periods",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("node_id", sa.BigInteger(), sa.ForeignKey("nodes.id"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("set_by", sa.String(16), nullable=False),
    )
    op.create_index("ix_node_bench_periods_node_id", "node_bench_periods", ["node_id"])


def downgrade() -> None:
    op.drop_index("ix_node_bench_periods_node_id", table_name="node_bench_periods")
    op.drop_table("node_bench_periods")
