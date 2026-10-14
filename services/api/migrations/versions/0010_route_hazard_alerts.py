"""watched routes: hazard_min_tier and the hazards already alerted

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0010"
down_revision = "0009"


def upgrade() -> None:
    op.add_column(
        "watched_routes",
        sa.Column("hazard_min_tier", sa.String(16), nullable=False, server_default="corroborated"),
    )
    op.add_column(
        "watched_routes",
        sa.Column("hazard_state", pg.JSONB, nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("watched_routes", "hazard_state")
    op.drop_column("watched_routes", "hazard_min_tier")
