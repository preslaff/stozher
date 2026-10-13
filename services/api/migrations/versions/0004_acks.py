"""signed acknowledgements

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade() -> None:
    op.create_table(
        "acks",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("node_id", sa.BigInteger, sa.ForeignKey("nodes.id"), nullable=False),
        sa.Column("key_id", sa.SmallInteger, nullable=False),
        sa.Column("cloud_key_id", sa.SmallInteger, nullable=False),
        sa.Column("max_seq", sa.Integer, nullable=False),
        sa.Column("mask", sa.Integer, nullable=False),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signature", sa.LargeBinary, nullable=False),
        sa.UniqueConstraint("node_id", "key_id", "cloud_key_id", "max_seq", "mask"),
    )
    op.create_index("ix_acks_node_key_signed", "acks", ["node_id", "key_id", "signed_at"])


def downgrade() -> None:
    op.drop_table("acks")
