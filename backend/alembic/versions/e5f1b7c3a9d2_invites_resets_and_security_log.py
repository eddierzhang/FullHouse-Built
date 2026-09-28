"""invites, password reset links, the security log, and turning access off

Revision ID: e5f1b7c3a9d2
Revises: c8d4a2e6f1b9
Create Date: 2026-09-28 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f1b7c3a9d2'
down_revision: Union[str, Sequence[str], None] = 'c8d4a2e6f1b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('disabled_at', sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        'invites',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('restaurant_id', sa.String(length=32), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('email', sa.String(length=254), nullable=True),
        sa.Column('created_by', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('accepted_by', sa.String(length=32), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], ondelete='CASCADE',
                                name='fk_invites_restaurant_id_restaurants'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL', name='fk_invites_created_by_users'),
        sa.ForeignKeyConstraint(['accepted_by'], ['users.id'], ondelete='SET NULL', name='fk_invites_accepted_by_users'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash', name='uq_invites_token_hash'),
    )
    op.create_index('ix_invites_restaurant_id', 'invites', ['restaurant_id'])

    op.create_table(
        'password_resets',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('created_by', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE', name='fk_password_resets_user_id_users'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL',
                                name='fk_password_resets_created_by_users'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash', name='uq_password_resets_token_hash'),
    )
    op.create_index('ix_password_resets_user_id', 'password_resets', ['user_id'])

    op.create_table(
        'security_events',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('restaurant_id', sa.String(length=32), nullable=True),
        sa.Column('type', sa.String(length=32), nullable=False),
        sa.Column('email', sa.String(length=254), nullable=True),
        sa.Column('actor_email', sa.String(length=254), nullable=True),
        sa.Column('ip', sa.String(length=64), nullable=True),
        sa.Column('user_agent', sa.String(length=255), nullable=True),
        sa.Column('detail', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_security_events_ts', 'security_events', ['ts'])
    op.create_index('ix_security_events_restaurant_id', 'security_events', ['restaurant_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_security_events_restaurant_id', table_name='security_events')
    op.drop_index('ix_security_events_ts', table_name='security_events')
    op.drop_table('security_events')
    op.drop_index('ix_password_resets_user_id', table_name='password_resets')
    op.drop_table('password_resets')
    op.drop_index('ix_invites_restaurant_id', table_name='invites')
    op.drop_table('invites')
    op.drop_column('users', 'disabled_at')
