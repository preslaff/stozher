"""initial: nodes, keys, transports, raw messages, events, api keys, audit log

Revision ID: 0001
Revises:
"""

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001"
down_revision = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.create_table(
        "nodes",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False, start=1), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column(
            "surveyed_position",
            geoalchemy2.Geometry("POINT", srid=4326, spatial_index=False),
            nullable=True,
        ),
        sa.Column("sky_mask", pg.JSONB, nullable=True),
        sa.Column("reliability_score", sa.Float, nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("last_seen", TS, nullable=True),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("id between 1 and 4294967295", name="ck_nodes_uint32"),
        sa.CheckConstraint("kind in ('expert','tbeam','phone','sim')", name="ck_nodes_kind"),
    )
    op.create_table(
        "node_keys",
        sa.Column("node_id", sa.BigInteger, sa.ForeignKey("nodes.id"), primary_key=True),
        sa.Column("key_id", sa.SmallInteger, primary_key=True),
        sa.Column("master_key_enc", sa.LargeBinary, nullable=False),
        sa.Column("valid_from", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("valid_to", TS, nullable=True),
        sa.CheckConstraint("key_id between 0 and 15", name="ck_node_keys_key_id"),
    )
    op.create_table(
        "transport_ids",
        sa.Column("id", sa.Integer, sa.Identity(), primary_key=True),
        sa.Column("node_id", sa.BigInteger, sa.ForeignKey("nodes.id"), nullable=False),
        sa.Column("transport", sa.String(16), nullable=False),
        sa.Column("external_id", sa.String(64), nullable=False),
        sa.UniqueConstraint("transport", "external_id"),
    )
    op.create_table(
        "raw_messages",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("transport", sa.String(16), nullable=False),
        sa.Column("external_id", sa.String(64), nullable=False),
        sa.Column("payload_hex", sa.Text, nullable=False),
        sa.Column("received_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("transport_meta", pg.JSONB, nullable=True),
        sa.Column("node_id", sa.BigInteger, nullable=True),
        sa.Column("verify_status", sa.String(16), nullable=False),
        sa.Column("reject_reason", sa.String(48), nullable=True),
    )
    op.create_index("ix_raw_messages_received_at", "raw_messages", ["received_at"])
    op.create_table(
        "events",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("node_id", sa.BigInteger, sa.ForeignKey("nodes.id"), nullable=False),
        sa.Column("key_id", sa.SmallInteger, nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("msg_type", sa.SmallInteger, nullable=False),
        sa.Column("event_class", sa.SmallInteger, nullable=False),
        sa.Column("confidence", sa.SmallInteger, nullable=False),
        sa.Column("flags", pg.JSONB, nullable=False),
        sa.Column(
            "position",
            geoalchemy2.Geometry("POINT", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column("observed_at", TS, nullable=False),
        sa.Column("duration_s", sa.Integer, nullable=False),
        sa.Column("features", pg.JSONB, nullable=False),
        sa.Column("classifier", pg.JSONB, nullable=True),
        sa.Column("checks", pg.JSONB, nullable=True),
        sa.Column("origin", sa.String(8), nullable=False),
        sa.Column("received_via", sa.String(16), nullable=False),
        sa.Column("ingested_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("payload_hex", sa.Text, nullable=False),
        sa.Column("sim", sa.Boolean, nullable=False),
        sa.UniqueConstraint("node_id", "key_id", "seq"),
    )
    op.create_index("ix_events_observed_at", "events", ["observed_at"])
    op.create_index("ix_events_position", "events", ["position"], postgresql_using="gist")
    op.create_table(
        "api_keys",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("prefix", sa.String(16), nullable=False, unique=True),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("owner_contact", sa.String(200), nullable=True),
        sa.Column("rate_limit_per_min", sa.Integer, nullable=True),
        sa.Column("allowed_origins", pg.JSONB, nullable=True),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(16), nullable=True),
        sa.Column("expires_at", TS, nullable=True),
        sa.Column("revoked_at", TS, nullable=True),
        sa.Column("last_used_at", TS, nullable=True),
        sa.CheckConstraint(
            "role in ('admin','operator','responder','public','system')", name="ck_api_keys_role"
        ),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("actor", sa.String(32), nullable=False),
        sa.Column("action", sa.String(48), nullable=False),
        sa.Column("target", sa.String(64), nullable=False),
        sa.Column("at", TS, server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    for t in (
        "audit_log",
        "api_keys",
        "events",
        "raw_messages",
        "transport_ids",
        "node_keys",
        "nodes",
    ):
        op.drop_table(t)
