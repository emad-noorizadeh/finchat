"""add always_load to sub_agent_templates

Revision ID: 90c477409a1a
Revises: b101e3c2cba5
Create Date: 2026-05-12 22:37:11.484211

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '90c477409a1a'
down_revision: Union[str, None] = 'b101e3c2cba5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default="0" is required so the ALTER succeeds against existing
    # populated tables — without it SQLite cannot backfill NOT NULL rows.
    with op.batch_alter_table('sub_agent_templates', schema=None) as batch_op:
        batch_op.add_column(sa.Column('always_load', sa.Boolean(), nullable=False, server_default=sa.text("0")))


def downgrade() -> None:
    with op.batch_alter_table('sub_agent_templates', schema=None) as batch_op:
        batch_op.drop_column('always_load')
