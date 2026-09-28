"""suppliers, menu, orders, agent runs and proposals, and the change log per restaurant

The rest of the restaurant's data gets a restaurant, as inventory did in
e8b3c6a1f4d7 and staff in b6e2f8a4c9d3, so nothing is shared between two
restaurants on one site. Order lines, recipe lines and run events follow
their order, dish and run. The change log's column is a plain value like
its other ids: an audit row outlives what it describes.

Everything already here is the site's restaurant's.

Revision ID: c8d4a2e6f1b9
Revises: b6e2f8a4c9d3
Create Date: 2026-09-28 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8d4a2e6f1b9'
down_revision: Union[str, Sequence[str], None] = 'b6e2f8a4c9d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SITE_RESTAURANT = "(SELECT id FROM restaurants ORDER BY created_at LIMIT 1)"
#: Tables that get a foreign key to their restaurant.
LINKED = ('suppliers', 'menu_items', 'orders', 'agent_runs', 'agent_actions')


def upgrade() -> None:
    """Upgrade schema."""
    for table in LINKED:
        # Batch mode: SQLite can only add a foreign key by rebuilding the table.
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column('restaurant_id', sa.String(length=32), nullable=True))
            batch.create_foreign_key(f'fk_{table}_restaurant_id_restaurants', 'restaurants', ['restaurant_id'], ['id'])
            batch.create_index(f'ix_{table}_restaurant_id', ['restaurant_id'])
    op.add_column('change_log', sa.Column('restaurant_id', sa.String(length=32), nullable=True))
    op.create_index('ix_change_log_restaurant_id', 'change_log', ['restaurant_id'])

    # With no restaurant set up yet they stay unclaimed, and setup claims them.
    for table in (*LINKED, 'change_log'):
        op.execute(f"UPDATE {table} SET restaurant_id = {SITE_RESTAURANT} WHERE restaurant_id IS NULL")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_change_log_restaurant_id', table_name='change_log')
    op.drop_column('change_log', 'restaurant_id')
    for table in reversed(LINKED):
        with op.batch_alter_table(table) as batch:
            batch.drop_index(f'ix_{table}_restaurant_id')
            batch.drop_constraint(f'fk_{table}_restaurant_id_restaurants', type_='foreignkey')
            batch.drop_column('restaurant_id')
