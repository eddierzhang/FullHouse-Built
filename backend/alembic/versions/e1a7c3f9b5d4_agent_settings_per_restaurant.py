"""each restaurant's own agent schedule and pause switch

Revision ID: e1a7c3f9b5d4
Revises: d6f8b2c4e0a3
Create Date: 2026-10-08 12:00:00.000000

A site can now hold many restaurants, each signed up by its own owner, so
the agent's schedule and its on/off switch can't live only on the shared
agent definition: one restaurant pausing it would pause it for all. Null
means "use the definition's", so an existing restaurant keeps the schedule
it has.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1a7c3f9b5d4'
down_revision: Union[str, Sequence[str], None] = 'd6f8b2c4e0a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('restaurants', sa.Column('agent_settings', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('restaurants') as batch:
        batch.drop_column('agent_settings')
