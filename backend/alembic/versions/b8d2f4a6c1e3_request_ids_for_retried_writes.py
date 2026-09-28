"""request ids, so a retried stock change or sale is recorded once

Revision ID: b8d2f4a6c1e3
Revises: a3c7e9b1d5f2
Create Date: 2026-09-28 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8d2f4a6c1e3'
down_revision: Union[str, Sequence[str], None] = 'a3c7e9b1d5f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Batch mode: SQLite can only add a constraint by rebuilding the table.
    with op.batch_alter_table('inventory_adjustments') as batch:
        batch.add_column(sa.Column('request_id', sa.String(length=64), nullable=True))
        batch.create_unique_constraint('uq_inventory_adjustments_request', ['restaurant_id', 'request_id'])
    with op.batch_alter_table('orders') as batch:
        batch.add_column(sa.Column('request_id', sa.String(length=64), nullable=True))
        batch.create_unique_constraint('uq_orders_request', ['restaurant_id', 'request_id'])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('orders') as batch:
        batch.drop_constraint('uq_orders_request', type_='unique')
        batch.drop_column('request_id')
    with op.batch_alter_table('inventory_adjustments') as batch:
        batch.drop_constraint('uq_inventory_adjustments_request', type_='unique')
        batch.drop_column('request_id')
