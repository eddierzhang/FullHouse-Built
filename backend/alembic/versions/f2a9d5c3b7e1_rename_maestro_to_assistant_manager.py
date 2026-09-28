"""rename the agent from Maestro to the Assistant Manager

Data only. The key stays `maestro`, so schedules, runs and proposals are
untouched. Databases that ran d4f7b1c9e2a6 before the rename still hold
the old name and prompt; this brings them in line with new installs. Only
text still exactly as d4f7b1c9e2a6 wrote it is changed, so an agent the
owner renamed or re-prompted keeps their edits.

Revision ID: f2a9d5c3b7e1
Revises: e8b3c6a1f4d7
Create Date: 2026-09-28 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2a9d5c3b7e1'
down_revision: Union[str, Sequence[str], None] = 'e8b3c6a1f4d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_NAME, NEW_NAME = "Maestro", "Assistant Manager"
OLD_OPENING, NEW_OPENING = "You are Maestro, ", "You are the Assistant Manager, "

definitions = sa.table(
    'agent_definitions',
    sa.column('key', sa.String), sa.column('name', sa.String), sa.column('system_prompt', sa.Text),
)


def _rename(name_from: str, name_to: str, opening_from: str, opening_to: str) -> None:
    conn = op.get_bind()
    conn.execute(
        definitions.update()
        .where(definitions.c.key == "maestro", definitions.c.name == name_from)
        .values(name=name_to)
    )
    row = conn.execute(
        sa.select(definitions.c.system_prompt).where(definitions.c.key == "maestro")
    ).first()
    if row and row.system_prompt and row.system_prompt.startswith(opening_from):
        conn.execute(
            definitions.update()
            .where(definitions.c.key == "maestro")
            .values(system_prompt=opening_to + row.system_prompt[len(opening_from):])
        )


def upgrade() -> None:
    _rename(OLD_NAME, NEW_NAME, OLD_OPENING, NEW_OPENING)


def downgrade() -> None:
    _rename(NEW_NAME, OLD_NAME, NEW_OPENING, OLD_OPENING)
