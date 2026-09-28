"""HR profiles, staff and shifts per restaurant, and published schedules

Staff gain contact details, a hire date and manager notes; staff and
shifts gain a restaurant, like inventory in e8b3c6a1f4d7; shifts gain
`published_at`, None while a draft only managers see.

Existing staff and shifts are given the site's restaurant, and existing
shifts count as already published: staff were seeing them before this
migration, and should go on seeing them after it.

Revision ID: b6e2f8a4c9d3
Revises: f2a9d5c3b7e1
Create Date: 2026-09-28 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6e2f8a4c9d3'
down_revision: Union[str, Sequence[str], None] = 'f2a9d5c3b7e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SITE_RESTAURANT = "(SELECT id FROM restaurants ORDER BY created_at LIMIT 1)"


def upgrade() -> None:
    """Upgrade schema."""
    # Batch mode: SQLite can only add a foreign key by rebuilding the table.
    with op.batch_alter_table('staff') as batch:
        batch.add_column(sa.Column('restaurant_id', sa.String(length=32), nullable=True))
        batch.add_column(sa.Column('phone', sa.String(length=40), nullable=True))
        batch.add_column(sa.Column('emergency_contact', sa.String(length=200), nullable=True))
        batch.add_column(sa.Column('hire_date', sa.Date(), nullable=True))
        batch.add_column(sa.Column('notes', sa.Text(), nullable=True))
        batch.create_foreign_key('fk_staff_restaurant_id_restaurants', 'restaurants', ['restaurant_id'], ['id'])
        batch.create_index('ix_staff_restaurant_id', ['restaurant_id'])

    with op.batch_alter_table('shifts') as batch:
        batch.add_column(sa.Column('restaurant_id', sa.String(length=32), nullable=True))
        batch.add_column(sa.Column('published_at', sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key('fk_shifts_restaurant_id_restaurants', 'restaurants', ['restaurant_id'], ['id'])
        batch.create_index('ix_shifts_restaurant_id', ['restaurant_id'])

    # With no restaurant set up yet they stay unclaimed, and setup claims them.
    op.execute(f"UPDATE staff SET restaurant_id = {SITE_RESTAURANT} WHERE restaurant_id IS NULL")
    op.execute(f"UPDATE shifts SET restaurant_id = {SITE_RESTAURANT} WHERE restaurant_id IS NULL")
    op.execute("UPDATE shifts SET published_at = CURRENT_TIMESTAMP WHERE published_at IS NULL")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('shifts') as batch:
        batch.drop_index('ix_shifts_restaurant_id')
        batch.drop_constraint('fk_shifts_restaurant_id_restaurants', type_='foreignkey')
        batch.drop_column('published_at')
        batch.drop_column('restaurant_id')

    with op.batch_alter_table('staff') as batch:
        batch.drop_index('ix_staff_restaurant_id')
        batch.drop_constraint('fk_staff_restaurant_id_restaurants', type_='foreignkey')
        batch.drop_column('notes')
        batch.drop_column('hire_date')
        batch.drop_column('emergency_contact')
        batch.drop_column('phone')
        batch.drop_column('restaurant_id')
