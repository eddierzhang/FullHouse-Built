"""purchase orders: placed on approval, received by hand or by invoice

Revision ID: d4f8b2c6e1a7
Revises: c9e3a7d2b5f1
Create Date: 2026-10-27 16:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4f8b2c6e1a7'
down_revision: Union[str, Sequence[str], None] = 'c9e3a7d2b5f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'purchase_orders',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('restaurant_id', sa.String(length=32), nullable=True),
        sa.Column('supplier_id', sa.String(length=32), nullable=True),
        sa.Column('inventory_item_id', sa.String(length=32), nullable=False),
        sa.Column('item_name', sa.String(length=255), nullable=False),
        sa.Column('supplier_name', sa.String(length=255), nullable=True),
        sa.Column('quantity', sa.Float(), nullable=False),
        sa.Column('unit', sa.String(length=32), nullable=False),
        sa.Column('packs', sa.Float(), nullable=True),
        sa.Column('pack_unit', sa.String(length=32), nullable=True),
        sa.Column('estimated_cost', sa.Float(), nullable=True),
        sa.Column('status', sa.String(length=12), nullable=False, server_default='ordered'),
        sa.Column('ordered_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expected_on', sa.Date(), nullable=True),
        sa.Column('shipped_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('received_qty', sa.Float(), nullable=True),
        sa.Column('invoice_line_id', sa.String(length=32), nullable=True),
        sa.Column('action_id', sa.String(length=32), nullable=True),
        sa.Column('note', sa.String(length=255), nullable=True),
        sa.Column('placed_by', sa.String(length=254), nullable=True),
        sa.Column('received_by', sa.String(length=254), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], name='fk_purchase_orders_restaurant_id_restaurants'),
        sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id']),
        sa.ForeignKeyConstraint(['inventory_item_id'], ['inventory_items.id'], name='fk_purchase_orders_inventory_item_id_inventory_items'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_purchase_orders_restaurant_id', 'purchase_orders', ['restaurant_id'])
    op.create_index('ix_purchase_orders_inventory_item_id', 'purchase_orders', ['inventory_item_id'])
    op.create_index('ix_purchase_orders_status', 'purchase_orders', ['status'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_purchase_orders_status', table_name='purchase_orders')
    op.drop_index('ix_purchase_orders_inventory_item_id', table_name='purchase_orders')
    op.drop_index('ix_purchase_orders_restaurant_id', table_name='purchase_orders')
    op.drop_table('purchase_orders')
