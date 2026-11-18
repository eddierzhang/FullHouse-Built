"""platform: feature flags, event log, job queue, webhook inbox, connectors; run token split and cost

Revision ID: e5a9c3d7f2b1
Revises: d4f8b2c6e1a7
Create Date: 2026-10-27 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5a9c3d7f2b1'
down_revision: Union[str, Sequence[str], None] = 'd4f8b2c6e1a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'restaurant_flags',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('restaurant_id', sa.String(length=32), nullable=False),
        sa.Column('flag', sa.String(length=64), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], name='fk_restaurant_flags_restaurant_id_restaurants'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('restaurant_id', 'flag', name='uq_restaurant_flags_restaurant_flag'),
    )
    op.create_index('ix_restaurant_flags_restaurant_id', 'restaurant_flags', ['restaurant_id'])
    op.create_table(
        'events',
        sa.Column('seq', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('restaurant_id', sa.String(length=32), nullable=True),
        sa.Column('type', sa.String(length=64), nullable=False),
        sa.Column('aggregate_table', sa.String(length=64), nullable=True),
        sa.Column('aggregate_id', sa.String(length=64), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], name='fk_events_restaurant_id_restaurants'),
        sa.PrimaryKeyConstraint('seq'),
    )
    op.create_index('ix_events_restaurant_id', 'events', ['restaurant_id'])
    op.create_index('ix_events_type', 'events', ['type'])
    op.create_index('ix_events_occurred_at', 'events', ['occurred_at'])
    op.create_index('ix_events_restaurant_type_seq', 'events', ['restaurant_id', 'type', 'seq'])
    op.create_table(
        'jobs',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('restaurant_id', sa.String(length=32), nullable=True),
        sa.Column('queue', sa.String(length=32), nullable=False),
        sa.Column('singleton_key', sa.String(length=200), nullable=False),
        sa.Column('status_group', sa.String(length=32), nullable=False, server_default='open'),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False, server_default='queued'),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('max_attempts', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('run_after', sa.DateTime(timezone=True), nullable=False),
        sa.Column('leased_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('worker', sa.String(length=64), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], name='fk_jobs_restaurant_id_restaurants'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('singleton_key', 'status_group', name='uq_jobs_singleton_open'),
    )
    for column in ('restaurant_id', 'queue', 'singleton_key', 'status', 'run_after', 'created_at'):
        op.create_index(f'ix_jobs_{column}', 'jobs', [column])
    op.create_table(
        'webhook_inbox',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('provider', sa.String(length=32), nullable=False),
        sa.Column('external_id', sa.String(length=128), nullable=False),
        sa.Column('restaurant_id', sa.String(length=32), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('job_id', sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('provider', 'external_id', name='uq_webhook_inbox_provider_external'),
    )
    op.create_index('ix_webhook_inbox_restaurant_id', 'webhook_inbox', ['restaurant_id'])
    op.create_index('ix_webhook_inbox_received_at', 'webhook_inbox', ['received_at'])
    op.create_table(
        'connector_providers',
        sa.Column('provider', sa.String(length=32), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('label', sa.String(length=80), nullable=False),
        sa.Column('auth', sa.String(length=16), nullable=False),
        sa.Column('capabilities', sa.JSON(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('provider'),
    )
    op.create_table(
        'connections',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('restaurant_id', sa.String(length=32), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('provider', sa.String(length=32), nullable=False),
        sa.Column('credentials_enc', sa.Text(), nullable=True),
        sa.Column('cursor', sa.Text(), nullable=True),
        sa.Column('rate_state', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False, server_default='active'),
        sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('settings', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], name='fk_connections_restaurant_id_restaurants'),
        sa.ForeignKeyConstraint(['provider'], ['connector_providers.provider']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('restaurant_id', 'provider', name='uq_connections_restaurant_provider'),
    )
    op.create_index('ix_connections_restaurant_id', 'connections', ['restaurant_id'])
    with op.batch_alter_table('agent_runs') as batch:
        batch.add_column(sa.Column('tokens_in', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('tokens_out', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('cost_usd', sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('agent_runs') as batch:
        batch.drop_column('cost_usd')
        batch.drop_column('tokens_out')
        batch.drop_column('tokens_in')
    for table in ('connections', 'connector_providers', 'webhook_inbox', 'jobs', 'events', 'restaurant_flags'):
        op.drop_table(table)
