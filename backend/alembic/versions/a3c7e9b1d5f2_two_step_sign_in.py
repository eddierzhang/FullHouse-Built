"""two-step sign-in: authenticator secrets, backup codes, and pending sign-ins

Revision ID: a3c7e9b1d5f2
Revises: e5f1b7c3a9d2
Create Date: 2026-09-28 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3c7e9b1d5f2'
down_revision: Union[str, Sequence[str], None] = 'e5f1b7c3a9d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column('mfa_secret', sa.String(length=255), nullable=True))
        batch.add_column(sa.Column('mfa_pending_secret', sa.String(length=255), nullable=True))
        batch.add_column(sa.Column('mfa_enabled_at', sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column('mfa_backup_codes', sa.JSON(), nullable=True))
        batch.add_column(sa.Column('mfa_last_step', sa.Integer(), nullable=True))

    op.create_table(
        'mfa_challenges',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE', name='fk_mfa_challenges_user_id_users'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_mfa_challenges_user_id', 'mfa_challenges', ['user_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_mfa_challenges_user_id', table_name='mfa_challenges')
    op.drop_table('mfa_challenges')
    with op.batch_alter_table('users') as batch:
        batch.drop_column('mfa_last_step')
        batch.drop_column('mfa_backup_codes')
        batch.drop_column('mfa_enabled_at')
        batch.drop_column('mfa_pending_secret')
        batch.drop_column('mfa_secret')
