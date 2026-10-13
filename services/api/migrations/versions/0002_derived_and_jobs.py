"""derived tables (hotspots, trust cells) and the job queue

Revision ID: 0002
Revises: 0001
"""

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0002"
down_revision = "0001"

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "hotspots",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column(
            "centroid",
            geoalchemy2.Geometry("POINT", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column(
            "source_ellipse",
            geoalchemy2.Geometry("POLYGON", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column("ellipse", pg.JSONB, nullable=False),
        sa.Column("severity", sa.Float, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("first_seen", TS, nullable=False),
        sa.Column("last_seen", TS, nullable=False),
        sa.Column("event_count", sa.Integer, nullable=False),
        sa.Column("node_count", sa.Integer, nullable=False),
        sa.Column("sim", sa.Boolean, nullable=False),
        sa.Column("track", pg.JSONB, nullable=True),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_hotspots_centroid", "hotspots", ["centroid"], postgresql_using="gist")
    op.create_table(
        "hotspot_events",
        sa.Column(
            "hotspot_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("hotspots.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("event_id", pg.UUID(as_uuid=True), sa.ForeignKey("events.id"), primary_key=True),
    )
    op.create_table(
        "trust_cells",
        sa.Column("h3_index", sa.String(16), primary_key=True),
        sa.Column(
            "center", geoalchemy2.Geometry("POINT", srid=4326, spatial_index=False), nullable=False
        ),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("score", sa.SmallInteger, nullable=True),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("independent_sources", sa.Integer, nullable=False),
        sa.Column("source_kinds", pg.JSONB, nullable=False),
        sa.Column("newest_observed_at", TS, nullable=False),
        sa.Column("sim", sa.Boolean, nullable=False),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_trust_cells_center", "trust_cells", ["center"], postgresql_using="gist")
    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("payload", pg.JSONB, nullable=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("run_after", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", TS, nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
    )
    # At most one pending job per kind: ingest bursts collapse into a single recompute.
    op.create_index(
        "uq_jobs_pending_kind",
        "jobs",
        ["kind"],
        unique=True,
        postgresql_where=sa.text("state = 'pending'"),
    )


def downgrade() -> None:
    for t in ("jobs", "trust_cells", "hotspot_events", "hotspots"):
        op.drop_table(t)
