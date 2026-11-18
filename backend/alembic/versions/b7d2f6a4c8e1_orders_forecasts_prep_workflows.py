"""phase C: purchase order drafts, lines and sending; forecasts; prep sheets; workflows

Revision ID: b7d2f6a4c8e1
Revises: a1c5e9b3d7f2
Create Date: 2026-10-28 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d2f6a4c8e1'
down_revision: Union[str, Sequence[str], None] = 'a1c5e9b3d7f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('purchase_orders') as batch:
        batch.alter_column('inventory_item_id', existing_type=sa.String(length=32), nullable=True)
        batch.add_column(sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column('sent_by', sa.String(length=254), nullable=True))
    op.create_table(
        'po_lines',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('restaurant_id', sa.String(length=32), nullable=True),
        sa.Column('po_id', sa.String(length=32), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('item_id', sa.String(length=32), nullable=True),
        sa.Column('inventory_item_id', sa.String(length=32), nullable=True),
        sa.Column('description', sa.String(length=200), nullable=False),
        sa.Column('qty', sa.Float(), nullable=False),
        sa.Column('unit', sa.String(length=32), nullable=False),
        sa.Column('units', sa.Float(), nullable=True),
        sa.Column('unit_price_expected', sa.Float(), nullable=True),
        sa.Column('received_qty', sa.Float(), nullable=True),
        sa.Column('invoice_line_id', sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], name='fk_po_lines_restaurant'),
        sa.ForeignKeyConstraint(['po_id'], ['purchase_orders.id'], name='fk_po_lines_order', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['inventory_item_id'], ['inventory_items.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_po_lines_restaurant_order', 'po_lines', ['restaurant_id', 'po_id'])
    op.create_table(
        'forecasts',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('restaurant_id', sa.String(length=32), nullable=False),
        sa.Column('pos_item_id', sa.String(length=120), nullable=False),
        sa.Column('pos_item_name', sa.String(length=200), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('qty_pred', sa.Float(), nullable=False),
        sa.Column('qty_lo', sa.Float(), nullable=False),
        sa.Column('qty_hi', sa.Float(), nullable=False),
        sa.Column('model', sa.String(length=40), nullable=False),
        sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], name='fk_forecasts_restaurant'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('restaurant_id', 'pos_item_id', 'date', name='uq_forecasts_restaurant_item_date'),
    )
    op.create_index('ix_forecasts_restaurant_date', 'forecasts', ['restaurant_id', 'date'])
    op.create_table(
        'forecast_errors',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('restaurant_id', sa.String(length=32), nullable=False),
        sa.Column('pos_item_id', sa.String(length=120), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('qty_pred', sa.Float(), nullable=False),
        sa.Column('qty_actual', sa.Float(), nullable=False),
        sa.Column('abs_pct_err', sa.Float(), nullable=True),
        sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], name='fk_forecast_errors_restaurant'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('restaurant_id', 'pos_item_id', 'date', name='uq_forecast_errors_restaurant_item_date'),
    )
    op.create_index('ix_forecast_errors_restaurant_date', 'forecast_errors', ['restaurant_id', 'date'])
    op.create_table(
        'prep_sheets',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('restaurant_id', sa.String(length=32), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('lines', sa.JSON(), nullable=False),
        sa.Column('proposal_id', sa.String(length=32), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], name='fk_prep_sheets_restaurant'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_prep_sheets_restaurant_date', 'prep_sheets', ['restaurant_id', 'date'])
    op.create_table(
        'workflows',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('restaurant_id', sa.String(length=32), nullable=False),
        sa.Column('kind', sa.String(length=24), nullable=False),
        sa.Column('cron', sa.String(length=40), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('approval_required', sa.Boolean(), nullable=False),
        sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], name='fk_workflows_restaurant_id_restaurants'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('restaurant_id', 'kind', name='uq_workflows_restaurant_kind'),
    )
    op.create_index('ix_workflows_restaurant_id', 'workflows', ['restaurant_id'])


def downgrade() -> None:
    """Downgrade schema."""
    for table in ('workflows', 'prep_sheets', 'forecast_errors', 'forecasts', 'po_lines'):
        op.drop_table(table)
    with op.batch_alter_table('purchase_orders') as batch:
        batch.drop_column('sent_by')
        batch.drop_column('sent_at')
        batch.alter_column('inventory_item_id', existing_type=sa.String(length=32), nullable=False)
