"""simulated reports carry a team label of their own

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"


def upgrade() -> None:
    op.add_column("hazard_reports", sa.Column("sim_team", sa.String(40), nullable=True))


def downgrade() -> None:
    op.drop_column("hazard_reports", "sim_team")
