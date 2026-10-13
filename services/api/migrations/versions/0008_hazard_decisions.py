"""human decisions on hazards, end time, and the tier/status history

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("road_hazards", sa.Column("decision", sa.String(16), nullable=True))
    op.add_column("road_hazards", sa.Column("decided_by", sa.String(16), nullable=True))
    op.add_column("road_hazards", sa.Column("decision_reason", sa.String(32), nullable=True))
    op.add_column("road_hazards", sa.Column("decided_at", TS, nullable=True))
    op.add_column("road_hazards", sa.Column("ended_at", TS, nullable=True))
    op.add_column(
        "road_hazards", sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False)
    )
    op.create_index("ix_road_hazards_status", "road_hazards", ["status"])
    op.create_table(
        "hazard_history",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("hazard_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("event", sa.String(16), nullable=False),
        sa.Column("value", sa.String(24), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
    )
    op.create_index("ix_hazard_history_hazard_id", "hazard_history", ["hazard_id"])


def downgrade() -> None:
    op.drop_table("hazard_history")
    op.drop_index("ix_road_hazards_status", "road_hazards")
    for c in ("updated_at", "ended_at", "decided_at", "decision_reason", "decided_by", "decision"):
        op.drop_column("road_hazards", c)
