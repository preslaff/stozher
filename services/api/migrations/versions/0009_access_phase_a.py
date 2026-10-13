"""organisations, named staff accounts, two-person requests; credentials gain organisation, purpose, kind, user

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0009"
down_revision = "0008"

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "organisations",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("type", sa.String(24), nullable=False),
        sa.Column("contact_name", sa.String(120), nullable=False),
        sa.Column("contact_address", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("max_role", sa.String(16), nullable=False),
        sa.Column("agreement_version", sa.String(16), nullable=True),
        sa.Column("agreement_accepted_at", TS, nullable=True),
        sa.Column("review_date", sa.Date, nullable=True),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(48), nullable=False),
        sa.Column("onboarded_at", TS, nullable=True),
        sa.CheckConstraint(
            "status in ('proposed','active','suspended','offboarded')",
            name="ck_organisations_status",
        ),
    )
    op.create_table(
        "users",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organisation_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("organisations.id"),
            nullable=False,
        ),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("work_email", sa.String(200), nullable=False, unique=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(48), nullable=False),
        sa.Column("disabled_at", TS, nullable=True),
        sa.CheckConstraint("role in ('operator','admin')", name="ck_users_role"),
        sa.CheckConstraint("status in ('active','disabled')", name="ck_users_status"),
    )
    op.create_table(
        "admin_requests",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column("requested_by", sa.String(48), nullable=False),
        sa.Column("requested_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("decided_by", sa.String(48), nullable=True),
        sa.Column("decided_at", TS, nullable=True),
        sa.Column("result", pg.JSONB, nullable=True),
    )
    op.add_column(
        "api_keys",
        sa.Column(
            "organisation_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("organisations.id"),
            nullable=True,
        ),
    )
    op.add_column("api_keys", sa.Column("purpose", sa.String(200), nullable=True))
    op.add_column("api_keys", sa.Column("kind", sa.String(16), nullable=True))
    op.add_column(
        "api_keys",
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_index("ix_api_keys_organisation_id", "api_keys", ["organisation_id"])
    op.add_column("audit_log", sa.Column("actor_user_id", pg.UUID(as_uuid=True), nullable=True))
    op.add_column(
        "audit_log", sa.Column("bootstrap", sa.Boolean, nullable=False, server_default=sa.false())
    )
    op.alter_column("audit_log", "actor", type_=sa.String(48))
    op.alter_column("audit_log", "target", type_=sa.String(64))


def downgrade() -> None:
    op.drop_column("audit_log", "bootstrap")
    op.drop_column("audit_log", "actor_user_id")
    op.drop_index("ix_api_keys_organisation_id", "api_keys")
    for c in ("user_id", "kind", "purpose", "organisation_id"):
        op.drop_column("api_keys", c)
    op.drop_table("admin_requests")
    op.drop_table("users")
    op.drop_table("organisations")
