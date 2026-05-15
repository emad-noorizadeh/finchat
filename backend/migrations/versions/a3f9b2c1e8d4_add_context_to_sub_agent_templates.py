"""add context to sub_agent_templates

Per-sub-agent knowledge blob that gets auto-prepended to every LLM-calling
node's system prompt unless the node opts out via include_context=false.
Stored as plain text — Markdown is the suggested format but the runtime
treats it as opaque string and just substitutes it in.

Revision ID: a3f9b2c1e8d4
Revises: 90c477409a1a
Create Date: 2026-05-14 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3f9b2c1e8d4'
down_revision: Union[str, None] = '90c477409a1a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default="" is required so the ALTER succeeds against existing
    # populated tables — every prior row backfills to an empty context.
    with op.batch_alter_table('sub_agent_templates', schema=None) as batch_op:
        batch_op.add_column(sa.Column('context', sa.Text(), nullable=False, server_default=sa.text("''")))


def downgrade() -> None:
    with op.batch_alter_table('sub_agent_templates', schema=None) as batch_op:
        batch_op.drop_column('context')
