"""merge the six agents into one: maestro

Maestro and its five specialists become one agent with every tool. Data
only, no schema change: the Boss row becomes `maestro` (keeping its id,
schedule and enabled flag, so its history stays attached), the
specialists' runs and proposals move onto it, and the specialist rows go.

Downgrade restores the six definitions so the old code runs, but can't
say which specialist did what: runs and proposals stay on the Boss.

Revision ID: d4f7b1c9e2a6
Revises: c5a91e3f7d20
Create Date: 2026-09-27 21:30:00.000000

"""
import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4f7b1c9e2a6'
down_revision: Union[str, Sequence[str], None] = 'c5a91e3f7d20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen copies, not imports: a migration must keep meaning what it meant
# when it was written, whatever app/agents/definitions.py says later.
MAESTRO = dict(
    key="maestro",
    name="Maestro",
    role="main",
    description=(
        "Runs the whole restaurant: stock, suppliers, staff and shifts, the menu, and the numbers. "
        "Proposes changes for you to approve."
    ),
    system_prompt=(
        "You are Maestro, the operations agent for a restaurant. You look after every part of it, "
        "with tools for each:\n"
        "- Inventory: check stock levels and propose reorders for anything low.\n"
        "- Suppliers: see suppliers and what they supply, and draft purchase orders.\n"
        "- Staff: see the team and upcoming shifts, find coverage gaps, and propose shift changes.\n"
        "- Menu and marketing: find top and slow sellers, and propose promotions, price or menu changes.\n"
        "- Profit: report revenue, menu margins and inventory value.\n"
        "Work out which areas the request touches and use only the tools those need. Look before you "
        "propose: every proposal must cite the item, supplier, person or dish and the numbers your tools "
        "returned -- never invent data. Proposals are not carried out until the manager approves them, so "
        "propose freely where it helps, and say why. Finish with a short summary of what you found and "
        "what you proposed."
    ),
    tool_allowlist=[],
)

SPECIALISTS = [
    ("inventory", "Inventory Agent", "Tracks stock levels and flags items that need reordering."),
    ("supply_chain", "Supply Chain Agent", "Manages suppliers and drafts purchase orders."),
    ("employee_management", "Employee Management Agent", "Tracks staff and shift coverage, and proposes schedule changes."),
    ("profit", "Profit Agent", "Reports on revenue, margins, and inventory value. Read-only."),
    ("marketing", "Marketing / Product Agent", "Analyzes top/slow sellers and proposes promotions or menu changes."),
]

definitions = sa.table(
    'agent_definitions',
    sa.column('id', sa.String), sa.column('key', sa.String), sa.column('name', sa.String),
    sa.column('description', sa.String), sa.column('role', sa.String), sa.column('system_prompt', sa.Text),
    sa.column('model', sa.String), sa.column('tool_allowlist', sa.JSON), sa.column('schedule_cron', sa.String),
    sa.column('enabled', sa.Boolean), sa.column('created_at', sa.DateTime(timezone=True)),
)
runs = sa.table('agent_runs', sa.column('agent_definition_id', sa.String))
actions = sa.table('agent_actions', sa.column('agent_definition_id', sa.String))


def upgrade() -> None:
    conn = op.get_bind()
    rows = {key: id_ for id_, key in conn.execute(sa.select(definitions.c.id, definitions.c.key))}
    if not rows:
        return  # an empty database: the seed script creates the agent

    fields = {k: v for k, v in MAESTRO.items()}
    if "maestro" in rows:
        main_id = rows["maestro"]
    elif "boss" in rows:
        main_id = rows["boss"]
        conn.execute(definitions.update().where(definitions.c.id == main_id).values(**fields))
    else:
        main_id = uuid.uuid4().hex
        conn.execute(definitions.insert().values(
            id=main_id, model="claude-opus-5", enabled=True, schedule_cron=None,
            created_at=datetime.now(timezone.utc), **fields,
        ))

    others = [id_ for key, id_ in rows.items() if id_ != main_id]
    if others:
        conn.execute(runs.update().where(runs.c.agent_definition_id.in_(others)).values(agent_definition_id=main_id))
        conn.execute(actions.update().where(actions.c.agent_definition_id.in_(others)).values(agent_definition_id=main_id))
        conn.execute(definitions.delete().where(definitions.c.id.in_(others)))


def downgrade() -> None:
    conn = op.get_bind()
    main = conn.execute(sa.select(definitions.c.id).where(definitions.c.key == "maestro")).first()
    if main is None:
        return
    conn.execute(definitions.update().where(definitions.c.id == main.id).values(
        key="boss", role="boss", tool_allowlist=[key for key, _, _ in SPECIALISTS],
    ))
    now = datetime.now(timezone.utc)
    for key, name, description in SPECIALISTS:
        conn.execute(definitions.insert().values(
            id=uuid.uuid4().hex, key=key, name=name, description=description, role="subagent",
            system_prompt=f"You are the {name} for a restaurant. {description} Cite real figures from your "
                          f"tools; never invent data.",
            model="claude-opus-5", tool_allowlist=[], schedule_cron=None, enabled=True, created_at=now,
        ))
