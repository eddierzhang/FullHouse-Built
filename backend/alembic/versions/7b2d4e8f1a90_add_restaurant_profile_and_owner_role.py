"""add restaurant profile and owner role

Revision ID: 7b2d4e8f1a90
Revises: 3c1e7a9d52f4
Create Date: 2026-09-27 19:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7b2d4e8f1a90'
down_revision: Union[str, Sequence[str], None] = '3c1e7a9d52f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'restaurants',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=True),
        sa.Column('street', sa.String(length=200), nullable=True),
        sa.Column('city', sa.String(length=100), nullable=True),
        sa.Column('region', sa.String(length=100), nullable=True),
        sa.Column('postal_code', sa.String(length=20), nullable=True),
        sa.Column('country', sa.String(length=100), nullable=True),
        sa.Column('phone', sa.String(length=40), nullable=True),
        sa.Column('email', sa.String(length=254), nullable=True),
        sa.Column('website', sa.String(length=255), nullable=True),
        sa.Column('timezone', sa.String(length=64), nullable=False),
        sa.Column('opening_hours', sa.JSON(), nullable=True),
        sa.Column('inventory_settings', sa.JSON(), nullable=True),
        sa.Column('staff_roles', sa.JSON(), nullable=True),
        sa.Column('scheduling_rules', sa.JSON(), nullable=True),
        sa.Column('completed_steps', sa.JSON(), nullable=False),
        sa.Column('setup_completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    # Batch mode: SQLite can only add a foreign key by rebuilding the table.
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column('role', sa.String(length=20), server_default='owner', nullable=False))
        batch.add_column(sa.Column('restaurant_id', sa.String(length=32), nullable=True))
        batch.create_foreign_key(
            'fk_users_restaurant_id_restaurants', 'restaurants', ['restaurant_id'], ['id'], ondelete='SET NULL'
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('users') as batch:
        batch.drop_constraint('fk_users_restaurant_id_restaurants', type_='foreignkey')
        batch.drop_column('restaurant_id')
        batch.drop_column('role')
    op.drop_table('restaurants')
