"""phase A: sales and labor days, weekly P&L, CSV profiles, documents; invoices link to documents and accounting

Revision ID: f6b1d4e8a2c3
Revises: e5a9c3d7f2b1
Create Date: 2026-10-27 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6b1d4e8a2c3'
down_revision: Union[str, Sequence[str], None] = 'e5a9c3d7f2b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'sales_days',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('restaurant_id', sa.String(length=32), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('net_sales', sa.Float(), nullable=False),
        sa.Column('gross_sales', sa.Float(), nullable=True),
        sa.Column('covers', sa.Integer(), nullable=True),
        sa.Column('tax', sa.Float(), nullable=True),
        sa.Column('tips', sa.Float(), nullable=True),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('connection_id', sa.String(length=32), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], name='fk_sales_days_restaurant'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('restaurant_id', 'date', 'source', name='uq_sales_days_restaurant_date_source'),
    )
    op.create_index('ix_sales_days_restaurant_date', 'sales_days', ['restaurant_id', 'date'])
    op.create_table(
        'sales_items',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('restaurant_id', sa.String(length=32), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('pos_item_id', sa.String(length=120), nullable=False),
        sa.Column('pos_item_name', sa.String(length=200), nullable=False),
        sa.Column('qty', sa.Float(), nullable=False),
        sa.Column('net', sa.Float(), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('connection_id', sa.String(length=32), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], name='fk_sales_items_restaurant'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('restaurant_id', 'date', 'pos_item_id', 'source', name='uq_sales_items_restaurant_date_item_source'),
    )
    op.create_index('ix_sales_items_restaurant_date', 'sales_items', ['restaurant_id', 'date'])
    op.create_index('ix_sales_items_restaurant_item_date', 'sales_items', ['restaurant_id', 'pos_item_id', 'date'])
    op.create_table(
        'labor_days',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('restaurant_id', sa.String(length=32), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('hours', sa.Float(), nullable=False),
        sa.Column('dollars', sa.Float(), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('connection_id', sa.String(length=32), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], name='fk_labor_days_restaurant'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('restaurant_id', 'date', 'source', name='uq_labor_days_restaurant_date_source'),
    )
    op.create_index('ix_labor_days_restaurant_date', 'labor_days', ['restaurant_id', 'date'])
    op.create_table(
        'pnl_weeks',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('restaurant_id', sa.String(length=32), nullable=False),
        sa.Column('week_of', sa.Date(), nullable=False),
        sa.Column('purchases_food', sa.Float(), nullable=False),
        sa.Column('purchases_paper', sa.Float(), nullable=False),
        sa.Column('purchases_bev', sa.Float(), nullable=False),
        sa.Column('sales_net', sa.Float(), nullable=True),
        sa.Column('labor_dollars', sa.Float(), nullable=True),
        sa.Column('food_pct', sa.Float(), nullable=True),
        sa.Column('paper_pct', sa.Float(), nullable=True),
        sa.Column('labor_pct', sa.Float(), nullable=True),
        sa.Column('prime_pct', sa.Float(), nullable=True),
        sa.Column('missing', sa.JSON(), nullable=False),
        sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], name='fk_pnl_weeks_restaurant'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('restaurant_id', 'week_of', name='uq_pnl_weeks_restaurant_week'),
    )
    op.create_index('ix_pnl_weeks_restaurant_id', 'pnl_weeks', ['restaurant_id'])
    op.create_table(
        'csv_profiles',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('restaurant_id', sa.String(length=32), nullable=False),
        sa.Column('capability', sa.String(length=16), nullable=False),
        sa.Column('mapping', sa.JSON(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], name='fk_csv_profiles_restaurant'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('restaurant_id', 'capability', name='uq_csv_profiles_restaurant_capability'),
    )
    op.create_index('ix_csv_profiles_restaurant_id', 'csv_profiles', ['restaurant_id'])
    op.create_table(
        'documents',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('restaurant_id', sa.String(length=32), nullable=False),
        sa.Column('doc_type', sa.String(length=16), nullable=False),
        sa.Column('type_confidence', sa.Float(), nullable=True),
        sa.Column('file_key', sa.String(length=300), nullable=True),
        sa.Column('filename', sa.String(length=255), nullable=True),
        sa.Column('media_type', sa.String(length=80), nullable=True),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('external_id', sa.String(length=128), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('extracted', sa.JSON(), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('result_table', sa.String(length=40), nullable=True),
        sa.Column('result_id', sa.String(length=32), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], name='fk_documents_restaurant'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_documents_restaurant_created', 'documents', ['restaurant_id', 'created_at'])
    op.create_index('ix_documents_external_id', 'documents', ['external_id'])
    with op.batch_alter_table('supplier_invoices') as batch:
        batch.add_column(sa.Column('document_id', sa.String(length=32), nullable=True))
        batch.add_column(sa.Column('external_refs', sa.JSON(), nullable=False, server_default='{}'))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('supplier_invoices') as batch:
        batch.drop_column('external_refs')
        batch.drop_column('document_id')
    for table in ('documents', 'csv_profiles', 'pnl_weeks', 'labor_days', 'sales_items', 'sales_days'):
        op.drop_table(table)
