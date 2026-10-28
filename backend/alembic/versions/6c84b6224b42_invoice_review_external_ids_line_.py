"""invoice review, external ids, line confidence, digest emailed_at

Revision ID: 6c84b6224b42
Revises: a7c2e9f4b1d6
Create Date: 2026-10-26 21:43:14.568632

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6c84b6224b42'
down_revision: Union[str, Sequence[str], None] = 'a7c2e9f4b1d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('price_digests', sa.Column('emailed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('supplier_invoice_lines', sa.Column('confidence', sa.Float(), nullable=True))
    op.add_column('supplier_invoices', sa.Column('total', sa.Float(), nullable=True))
    # Everything already in was typed or imported by a person and counted: complete.
    op.add_column('supplier_invoices', sa.Column('status', sa.String(length=16), nullable=False, server_default='complete'))
    op.add_column('supplier_invoices', sa.Column('review_reasons', sa.JSON(), nullable=False, server_default='[]'))
    op.add_column('supplier_invoices', sa.Column('external_id', sa.String(length=64), nullable=True))
    op.create_index(op.f('ix_supplier_invoices_external_id'), 'supplier_invoices', ['external_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_supplier_invoices_external_id'), table_name='supplier_invoices')
    op.drop_column('supplier_invoices', 'external_id')
    op.drop_column('supplier_invoices', 'review_reasons')
    op.drop_column('supplier_invoices', 'status')
    op.drop_column('supplier_invoices', 'total')
    op.drop_column('supplier_invoice_lines', 'confidence')
    op.drop_column('price_digests', 'emailed_at')
