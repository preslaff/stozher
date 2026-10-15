"""optional modules report that they are alive (health: present or absent)

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"


def upgrade() -> None:
    op.create_table(
        "module_heartbeats",
        sa.Column("module", sa.String(32), primary_key=True),
        sa.Column(
            "last_seen", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("info", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("module_heartbeats")
