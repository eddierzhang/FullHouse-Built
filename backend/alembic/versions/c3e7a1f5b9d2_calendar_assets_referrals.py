"""phase D: calendar items, assets, referrals

Revision ID: c3e7a1f5b9d2
Revises: b7d2f6a4c8e1
Create Date: 2026-10-28 02:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3e7a1f5b9d2'
down_revision: Union[str, Sequence[str], None] = 'b7d2f6a4c8e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'calendar_items',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('restaurant_id', sa.String(length=32), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('due_on', sa.Date(), nullable=False),
        sa.Column('recurrence', sa.String(length=40), nullable=True),
        sa.Column('source_document_id', sa.String(length=32), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('proof_document_id', sa.String(length=32), nullable=True),
        sa.Column('asset_id', sa.String(length=32), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], name='fk_calendar_items_restaurant'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_calendar_items_restaurant_due', 'calendar_items', ['restaurant_id', 'due_on'])
    op.create_table(
        'assets',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('restaurant_id', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('category', sa.String(length=60), nullable=True),
        sa.Column('vendor', sa.String(length=200), nullable=True),
        sa.Column('purchased_on', sa.Date(), nullable=True),
        sa.Column('warranty_until', sa.Date(), nullable=True),
        sa.Column('service_interval_days', sa.Integer(), nullable=True),
        sa.Column('last_service_on', sa.Date(), nullable=True),
        sa.Column('source_document_id', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], name='fk_assets_restaurant'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_assets_restaurant_name', 'assets', ['restaurant_id', 'name'])
    op.create_table(
        'referrals',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('restaurant_id', sa.String(length=32), nullable=False),
        sa.Column('referred_email', sa.String(length=254), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('rewarded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], name='fk_referrals_restaurant'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_referrals_restaurant_created', 'referrals', ['restaurant_id', 'created_at'])


def downgrade() -> None:
    """Downgrade schema."""
    for table in ('referrals', 'assets', 'calendar_items'):
        op.drop_table(table)
