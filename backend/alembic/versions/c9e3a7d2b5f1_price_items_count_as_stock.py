"""price items count as inventory items; invoice lines remember when they were booked into stock

Revision ID: c9e3a7d2b5f1
Revises: 6c84b6224b42
Create Date: 2026-10-27 15:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9e3a7d2b5f1'
down_revision: Union[str, Sequence[str], None] = '6c84b6224b42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('price_items') as batch:
        batch.add_column(sa.Column('inventory_item_id', sa.String(length=32), nullable=True))
        batch.add_column(sa.Column('inventory_link', sa.String(length=8), nullable=False, server_default='auto'))
        batch.create_index('ix_price_items_inventory_item_id', ['inventory_item_id'])
        batch.create_foreign_key('fk_price_items_inventory_item_id_inventory_items', 'inventory_items',
                                 ['inventory_item_id'], ['id'], ondelete='SET NULL')
    op.add_column('supplier_invoice_lines', sa.Column('stocked_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('supplier_invoice_lines', 'stocked_at')
    with op.batch_alter_table('price_items') as batch:
        batch.drop_constraint('fk_price_items_inventory_item_id_inventory_items', type_='foreignkey')
        batch.drop_index('ix_price_items_inventory_item_id')
        batch.drop_column('inventory_link')
        batch.drop_column('inventory_item_id')
