"""inventory per restaurant, and a history of stock adjustments

Revision ID: e8b3c6a1f4d7
Revises: d4f7b1c9e2a6
Create Date: 2026-09-28 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8b3c6a1f4d7'
down_revision: Union[str, Sequence[str], None] = 'd4f7b1c9e2a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable first, filled in, then tightened: the table may already hold stock.
    # Batch mode: SQLite can only add a foreign key or tighten a column by rebuilding.
    with op.batch_alter_table('inventory_items') as batch:
        batch.add_column(sa.Column('restaurant_id', sa.String(length=32), nullable=True))
        batch.add_column(sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key(
            'fk_inventory_items_restaurant_id_restaurants', 'restaurants', ['restaurant_id'], ['id']
        )
        batch.create_index('ix_inventory_items_restaurant_id', ['restaurant_id'])

    # Existing stock is the site's restaurant's. With none set up yet it stays
    # unclaimed, and setup claims it (app/restaurant/inventory.py).
    op.execute(
        "UPDATE inventory_items SET restaurant_id = "
        "(SELECT id FROM restaurants ORDER BY created_at LIMIT 1) WHERE restaurant_id IS NULL"
    )
    op.execute("UPDATE inventory_items SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL")
    with op.batch_alter_table('inventory_items') as batch:
        batch.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), nullable=False)

    op.create_table(
        'inventory_adjustments',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('restaurant_id', sa.String(length=32), nullable=True),
        sa.Column('inventory_item_id', sa.String(length=32), nullable=False),
        sa.Column('change', sa.Float(), nullable=False),
        sa.Column('quantity_after', sa.Float(), nullable=False),
        sa.Column('reason', sa.String(length=20), nullable=False),
        sa.Column('note', sa.String(length=255), nullable=True),
        sa.Column('actor_type', sa.String(length=16), nullable=False),
        sa.Column('actor_id', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['restaurant_id'], ['restaurants.id'], name='fk_inventory_adjustments_restaurant_id_restaurants'
        ),
        sa.ForeignKeyConstraint(
            ['inventory_item_id'], ['inventory_items.id'], ondelete='CASCADE',
            name='fk_inventory_adjustments_item_id_inventory_items',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_inventory_adjustments_restaurant_id', 'inventory_adjustments', ['restaurant_id'])
    op.create_index('ix_inventory_adjustments_inventory_item_id', 'inventory_adjustments', ['inventory_item_id'])
    op.create_index('ix_inventory_adjustments_created_at', 'inventory_adjustments', ['created_at'])

    # History starts here: one entry per item explaining the stock it already
    # has, so every item's history adds up to its quantity on hand.
    op.execute(
        "INSERT INTO inventory_adjustments "
        "(restaurant_id, inventory_item_id, change, quantity_after, reason, note, actor_type, actor_id, created_at) "
        "SELECT restaurant_id, id, quantity_on_hand, quantity_on_hand, 'initial', "
        "'On hand when stock history began', 'system', 'system', CURRENT_TIMESTAMP "
        "FROM inventory_items WHERE quantity_on_hand <> 0"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_inventory_adjustments_created_at', table_name='inventory_adjustments')
    op.drop_index('ix_inventory_adjustments_inventory_item_id', table_name='inventory_adjustments')
    op.drop_index('ix_inventory_adjustments_restaurant_id', table_name='inventory_adjustments')
    op.drop_table('inventory_adjustments')
    with op.batch_alter_table('inventory_items') as batch:
        batch.drop_index('ix_inventory_items_restaurant_id')
        batch.drop_constraint('fk_inventory_items_restaurant_id_restaurants', type_='foreignkey')
        batch.drop_column('updated_at')
        batch.drop_column('restaurant_id')
