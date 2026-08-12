"""add parameters to sub_agent_templates

Planner-fillable parameters declared per sub-agent (agent-level, synced
across channel variants). Merged into the entry tool's OpenAI function
schema so the orchestrator LLM fills them when invoking the sub-agent;
valid values seed the inner graph's `variables` so parse_node can skip
or narrow its extraction LLM call. Empty object (default) keeps existing
rows byte-identical in behaviour.

Revision ID: d8f2a6b3c1e9
Revises: c5d8e2a1f9b7
Create Date: 2026-08-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd8f2a6b3c1e9'
down_revision: Union[str, None] = 'c5d8e2a1f9b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('sub_agent_templates', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'parameters',
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ))


def downgrade() -> None:
    with op.batch_alter_table('sub_agent_templates', schema=None) as batch_op:
        batch_op.drop_column('parameters')
