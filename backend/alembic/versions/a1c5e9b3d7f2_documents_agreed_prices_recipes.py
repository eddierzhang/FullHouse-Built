"""phase B: agreed prices, recipes, recipe lines, plate costs

Revision ID: a1c5e9b3d7f2
Revises: f6b1d4e8a2c3
Create Date: 2026-10-27 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c5e9b3d7f2'
down_revision: Union[str, Sequence[str], None] = 'f6b1d4e8a2c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'agreed_prices',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('restaurant_id', sa.String(length=32), nullable=False),
        sa.Column('supplier_id', sa.String(length=32), nullable=False),
        sa.Column('item_id', sa.String(length=32), nullable=False),
        sa.Column('unit_price', sa.Float(), nullable=False),
        sa.Column('base_unit', sa.String(length=32), nullable=False),
        sa.Column('effective_from', sa.Date(), nullable=False),
        sa.Column('effective_to', sa.Date(), nullable=True),
        sa.Column('source_document_id', sa.String(length=32), nullable=True),
        sa.Column('confirmed_by', sa.String(length=254), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], name='fk_agreed_prices_restaurant'),
        sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id']),
        sa.ForeignKeyConstraint(['item_id'], ['price_items.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_agreed_prices_restaurant_item_supplier', 'agreed_prices', ['restaurant_id', 'item_id', 'supplier_id'])
    op.create_table(
        'recipes',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('restaurant_id', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('pos_item_id', sa.String(length=120), nullable=True),
        sa.Column('menu_item_id', sa.String(length=32), nullable=True),
        sa.Column('yield_qty', sa.Float(), nullable=False),
        sa.Column('yield_unit', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('source', sa.String(length=10), nullable=False),
        sa.Column('document_id', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], name='fk_recipes_restaurant'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_recipes_restaurant_status', 'recipes', ['restaurant_id', 'status'])
    op.create_table(
        'recipe_lines',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('restaurant_id', sa.String(length=32), nullable=False),
        sa.Column('recipe_id', sa.String(length=32), nullable=False),
        sa.Column('position', sa.Float(), nullable=False),
        sa.Column('item_id', sa.String(length=32), nullable=True),
        sa.Column('sub_recipe_id', sa.String(length=32), nullable=True),
        sa.Column('qty', sa.Float(), nullable=False),
        sa.Column('unit', sa.String(length=32), nullable=False),
        sa.Column('ingredient_text', sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], name='fk_recipe_lines_restaurant'),
        sa.ForeignKeyConstraint(['recipe_id'], ['recipes.id'], name='fk_recipe_lines_recipe', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['item_id'], ['price_items.id']),
        sa.ForeignKeyConstraint(['sub_recipe_id'], ['recipes.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_recipe_lines_restaurant_recipe', 'recipe_lines', ['restaurant_id', 'recipe_id'])
    op.create_table(
        'plate_costs',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('restaurant_id', sa.String(length=32), nullable=False),
        sa.Column('recipe_id', sa.String(length=32), nullable=False),
        sa.Column('week_of', sa.Date(), nullable=False),
        sa.Column('cost', sa.Float(), nullable=False),
        sa.Column('basis', sa.JSON(), nullable=False),
        sa.Column('unpriced', sa.JSON(), nullable=False),
        sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], name='fk_plate_costs_restaurant'),
        sa.ForeignKeyConstraint(['recipe_id'], ['recipes.id'], name='fk_plate_costs_recipe', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('recipe_id', 'week_of', name='uq_plate_costs_recipe_week'),
    )
    op.create_index('ix_plate_costs_restaurant_week', 'plate_costs', ['restaurant_id', 'week_of'])


def downgrade() -> None:
    """Downgrade schema."""
    for table in ('plate_costs', 'recipe_lines', 'recipes', 'agreed_prices'):
        op.drop_table(table)
