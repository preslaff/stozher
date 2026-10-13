"""alerts

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0003"
down_revision = "0002"


def upgrade() -> None:
    op.create_table(
        "alerts",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("area", pg.JSONB, nullable=True),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("hotspot_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("delivered", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("sim", sa.Boolean, nullable=False),
        sa.Column("dedupe_key", sa.String(80), nullable=False, unique=True),
        sa.CheckConstraint("level in ('info','warning','critical')", name="ck_alerts_level"),
    )


def downgrade() -> None:
    op.drop_table("alerts")
