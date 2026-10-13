"""reporter role, hazard reports and road hazards

Revision ID: 0007
Revises: 0006
"""

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0007"
down_revision = "0006"

TS = sa.DateTime(timezone=True)
OLD_ROLES = "('admin','operator','responder','public','system')"
NEW_ROLES = "('admin','operator','responder','reporter','public','system')"


def upgrade() -> None:
    op.drop_constraint("ck_api_keys_role", "api_keys", type_="check")
    op.create_check_constraint("ck_api_keys_role", "api_keys", f"role in {NEW_ROLES}")
    op.create_table(
        "hazard_reports",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("reporter_prefix", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("passability", sa.String(24), nullable=False),
        sa.Column(
            "geometry",
            geoalchemy2.Geometry("GEOMETRY", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column("observed_at", TS, nullable=False),
        sa.Column("received_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("sim", sa.Boolean, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("hazard_id", pg.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_hazard_reports_reporter_prefix", "hazard_reports", ["reporter_prefix"])
    op.create_index("ix_hazard_reports_received_at", "hazard_reports", ["received_at"])
    op.create_index(
        "ix_hazard_reports_geometry", "hazard_reports", ["geometry"], postgresql_using="gist"
    )
    op.create_table(
        "road_hazards",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("passability", sa.String(24), nullable=False),
        sa.Column(
            "geometry",
            geoalchemy2.Geometry("GEOMETRY", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column("tier", sa.String(16), nullable=False),
        sa.Column("sources", pg.JSONB, nullable=False),
        sa.Column("first_seen", TS, nullable=False),
        sa.Column("last_confirmed", TS, nullable=True),
        sa.Column("valid_until", TS, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("sim", sa.Boolean, nullable=False),
        sa.Column("evidence", pg.JSONB, nullable=True),
    )
    op.create_index(
        "ix_road_hazards_geometry", "road_hazards", ["geometry"], postgresql_using="gist"
    )


def downgrade() -> None:
    op.drop_table("road_hazards")
    op.drop_table("hazard_reports")
    op.execute(
        "UPDATE api_keys SET role = 'public' WHERE role = 'reporter'"
    )  # the role no longer exists
    op.drop_constraint("ck_api_keys_role", "api_keys", type_="check")
    op.create_check_constraint("ck_api_keys_role", "api_keys", f"role in {OLD_ROLES}")
