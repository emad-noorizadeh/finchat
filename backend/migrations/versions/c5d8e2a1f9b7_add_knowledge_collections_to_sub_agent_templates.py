"""add knowledge_collections to sub_agent_templates

Per-sub-agent allow-list of Chroma collections that knowledge_search may
read from when the sub-agent is the caller. Empty list (default) falls
back to the global "system_knowledge" collection so existing rows keep
their current behaviour.

Revision ID: c5d8e2a1f9b7
Revises: a3f9b2c1e8d4
Create Date: 2026-05-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c5d8e2a1f9b7'
down_revision: Union[str, None] = 'a3f9b2c1e8d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('sub_agent_templates', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'knowledge_collections',
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ))


def downgrade() -> None:
    with op.batch_alter_table('sub_agent_templates', schema=None) as batch_op:
        batch_op.drop_column('knowledge_collections')
