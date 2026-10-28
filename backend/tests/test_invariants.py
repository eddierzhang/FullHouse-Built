"""The v2 invariants, checked from the schema and the code rather than one table at a time.

Each holds in every phase. A phase is not done until these still pass.
"""

import ast
import pathlib

import pytest
from sqlalchemy import inspect as sa_inspect

from app.db.session import Base

APP = pathlib.Path(__file__).resolve().parent.parent / "app"

#: Tables that are the site's, not a restaurant's: one row serves every tenant.
GLOBAL_TABLES = {
    "agent_definitions", "connector_providers", "users", "auth_sessions", "invites", "password_resets",
    "security_events", "mfa_challenges", "restaurants", "feedback", "client_errors", "alembic_version",
}


def tenant_tables():
    return sorted(name for name, table in Base.metadata.tables.items()
                  if "restaurant_id" in table.c and name not in GLOBAL_TABLES)


def test_every_tenant_table_has_restaurant_id_and_an_index_that_leads_with_it():
    """I2: every tenant table carries restaurant_id and can be read per restaurant without a scan."""
    missing = []
    for name in tenant_tables():
        table = Base.metadata.tables[name]
        leads = [idx for idx in table.indexes if list(idx.columns)[0].name == "restaurant_id"]
        leads += [c for c in table.constraints if hasattr(c, "columns") and list(c.columns) and list(c.columns)[0].name == "restaurant_id"]
        if not leads:
            missing.append(name)
    assert missing == [], f"tenant tables with no index leading with restaurant_id: {missing}"


def test_tables_that_mean_a_restaurant_are_tenant_tables():
    """Every table in the metadata is either known to be global or carries restaurant_id."""
    unscoped = sorted(name for name, table in Base.metadata.tables.items()
                      if "restaurant_id" not in table.c and name not in GLOBAL_TABLES
                      # Children of a tenant row, scoped through their parent.
                      and name not in {"order_items", "recipe_items", "agent_events", "supplier_invoice_lines"})
    assert unscoped == [], f"tables with no restaurant_id and not listed as global: {unscoped}"


def test_no_price_is_stored_on_the_alias_table():
    """I5: the match between a printed line and an item is a fact about the item, never about money."""
    columns = {c.name for c in Base.metadata.tables["price_item_aliases"].c}
    assert not any(word in c for c in columns for word in ("price", "cost", "amount", "total"))


def test_the_agent_cannot_write_any_tenant_table_through_a_tool(db):
    """I1: inside a tool, every write outside the proposal tables is refused at flush time."""
    from app.agents.guard import ALLOWED_TABLES, ForbiddenAgentWrite, tool_scope

    classes = {m.local_table.name: m.class_ for m in Base.registry.mappers if m.local_table is not None}
    refused = []
    for name in tenant_tables():
        if name in ALLOWED_TABLES or name not in classes:
            continue
        obj = classes[name]()
        with tool_scope("a_tool"):
            db.add(obj)
            with pytest.raises(ForbiddenAgentWrite):
                db.flush()
        db.rollback()
        db.expunge_all()
        refused.append(name)
    assert len(refused) >= 20, refused
    # And a proposal, the one thing a tool is for, goes through.
    from app.agents.models import AgentAction, AgentDefinition, AgentRun

    definition = AgentDefinition(key="t", name="T", description="d", role="subagent", system_prompt="s")
    db.add(definition)
    db.flush()
    run = AgentRun(agent_definition_id=definition.id, status="running")
    db.add(run)
    db.flush()
    with tool_scope("propose"):
        db.add(AgentAction(run_id=run.id, agent_definition_id=definition.id, action_type="purchase_order", payload={}))
        db.flush()


def test_the_registry_wraps_every_tool_in_the_guard(db):
    from app.agents.definitions import AGENT_DEF
    from app.agents.models import AgentDefinition, AgentRun
    from app.agents.registry import resolve_tools

    columns = {c.key for c in sa_inspect(AgentDefinition).columns}
    definition = AgentDefinition(**{"role": "boss", **{k: v for k, v in dict(AGENT_DEF).items() if k in columns and v is not None}})
    db.add(definition)
    db.flush()
    run = AgentRun(agent_definition_id=definition.id, status="running")
    db.add(run)
    db.flush()
    tools = resolve_tools(db, run, definition)
    assert tools and all(t.call.__name__ == "call" and t.call.__qualname__.startswith("guarded") for t in tools)


def test_no_route_or_page_code_decrypts_credentials():
    """I8/security: credentials are decrypted only in the worker's registry, never where a request runs."""
    offenders = []
    for path in APP.rglob("*.py"):
        if path.name == "registry.py" and path.parent.name == "connectors" or path.name == "crypto.py":
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("connectors.crypto"):
                if any(alias.name == "decrypt" for alias in node.names):
                    offenders.append(str(path.relative_to(APP)))
            if isinstance(node, ast.Attribute) and node.attr == "decrypt" and isinstance(node.value, ast.Name) and node.value.id == "crypto":
                offenders.append(str(path.relative_to(APP)))
    assert offenders == []


def test_no_agent_code_imports_the_mail_client_or_a_connector_write():
    """I8: no path the agent runs on can send mail or call a connector."""
    offenders = []
    for path in (APP / "agents").rglob("*.py"):
        text = path.read_text()
        if "app.prices.mail" in text or "smtplib" in text or "app.connectors" in text:
            offenders.append(str(path.relative_to(APP)))
    assert offenders == []
