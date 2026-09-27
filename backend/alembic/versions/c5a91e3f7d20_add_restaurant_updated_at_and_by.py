"""add restaurant updated_at and updated_by

Revision ID: c5a91e3f7d20
Revises: 7b2d4e8f1a90
Create Date: 2026-09-27 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5a91e3f7d20'
down_revision: Union[str, Sequence[str], None] = '7b2d4e8f1a90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('restaurants', sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('restaurants', sa.Column('updated_by', sa.String(length=32), nullable=True))
    # A restaurant saved before this has no record of edits: its creation
    # is the last change anyone can vouch for.
    op.execute("UPDATE restaurants SET updated_at = created_at")
    # Batch mode: SQLite can only tighten a column by rebuilding the table.
    with op.batch_alter_table('restaurants') as batch:
        batch.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('restaurants') as batch:
        batch.drop_column('updated_by')
        batch.drop_column('updated_at')
