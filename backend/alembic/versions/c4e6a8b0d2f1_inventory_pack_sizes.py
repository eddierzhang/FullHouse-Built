"""pack sizes, so an item counted in each can be bought and delivered by the case

Revision ID: c4e6a8b0d2f1
Revises: b8d2f4a6c1e3
Create Date: 2026-09-28 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4e6a8b0d2f1'
down_revision: Union[str, Sequence[str], None] = 'b8d2f4a6c1e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable, no default: existing items simply aren't bought in packs yet.
    op.add_column('inventory_items', sa.Column('pack_unit', sa.String(length=32), nullable=True))
    op.add_column('inventory_items', sa.Column('pack_size', sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('inventory_items') as batch:
        batch.drop_column('pack_size')
        batch.drop_column('pack_unit')
