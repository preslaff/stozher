"""api key rotation grace

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("prev_hash", sa.String(64), nullable=True))
    op.add_column(
        "api_keys", sa.Column("prev_hash_expires_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("api_keys", "prev_hash_expires_at")
    op.drop_column("api_keys", "prev_hash")
