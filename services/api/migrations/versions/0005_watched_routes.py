"""watched routes and the alert route link

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0005"
down_revision = "0004"

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "watched_routes",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_prefix", sa.String(16), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("coordinates", pg.JSONB, nullable=False),
        sa.Column("buffer_m", sa.Integer, nullable=False),
        sa.Column("min_level", sa.String(16), nullable=False),
        sa.Column("notify", pg.JSONB, nullable=False),
        sa.Column("asset_type", sa.String(32), nullable=True),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("last_state", sa.String(16), nullable=True),
        sa.Column("transitions", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_evaluated_at", TS, nullable=True),
        sa.CheckConstraint(
            "min_level in ('degraded','jammed','spoofed')", name="ck_watched_routes_level"
        ),
    )
    op.create_index("ix_watched_routes_owner_prefix", "watched_routes", ["owner_prefix"])
    op.add_column("alerts", sa.Column("route_id", pg.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_alerts_route_id", "alerts", ["route_id"])


def downgrade() -> None:
    op.drop_index("ix_alerts_route_id", "alerts")
    op.drop_column("alerts", "route_id")
    op.drop_table("watched_routes")
