"""One agent, the Assistant Manager, runs every part of the restaurant."""

from datetime import datetime, timezone

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from app.agents import crud, scheduler
from app.agents.definitions import AGENT_DEF, AGENT_KEY
from app.agents.models import AgentAction, AgentDefinition, AgentRun
from app.agents.providers.base import LLMProvider, ProviderResult
from app.agents.registry import TOOL_AREAS, resolve_tools
from app.agents.runner import execute_run
from app.restaurant.models import InventoryItem, Staff, Supplier
from test_migrations import _alembic_config


@pytest.fixture
def maestro(db):
    defn = AgentDefinition(role="main", model="m", enabled=True, **AGENT_DEF)
    db.add(defn)
    db.commit()
    return defn


def test_the_one_agent_has_every_tool_tagged_by_area(db, maestro):
    run = crud.create_run(db, maestro.id, input="x")
    tools = resolve_tools(db, run, maestro)

    names = [t.name for t in tools]
    assert len(names) == len(set(names))  # no two areas share a tool name
    assert {t.area for t in tools} == set(TOOL_AREAS)
    assert {"list_low_stock_items", "draft_purchase_order", "propose_shift_change",
            "draft_menu_change_proposal", "get_revenue_summary"} <= set(names)
    assert not any(n.startswith("delegate_to_") for n in names)


def test_an_allowlist_narrows_it_to_some_areas(db, maestro):
    maestro.tool_allowlist = ["profit"]
    run = crud.create_run(db, maestro.id, input="x")
    assert {t.area for t in resolve_tools(db, run, maestro)} == {"profit"}


class CrossAreaProvider(LLMProvider):
    """Uses tools from three areas in one run, as a model asked for a review would."""

    key = "fake"

    def __init__(self, calls):
        self.calls = calls

    def resolve_model(self, configured_model):
        return "fake"

    def run_agent_loop(self, *, model, system, tools, user_message, emit, should_cancel=None):
        by_name = {t.name: t for t in tools}
        for name, args in self.calls:
            emit("tool_call", {"tool": name, "input": args})
            emit("tool_result", {"tool": name, "result": str(by_name[name].call(args))})
        return ProviderResult(text="Reordered tomatoes, drafted an order, covered Friday.")


def test_one_run_can_propose_across_the_whole_restaurant(db, maestro, monkeypatch):
    supplier = Supplier(name="Green Valley", lead_time_days=1)
    db.add(supplier)
    db.flush()
    tomatoes = InventoryItem(name="Tomatoes", unit="lb", quantity_on_hand=2, reorder_threshold=10,
                             reorder_qty=20, supplier_id=supplier.id)
    ana = Staff(name="Ana", role="Server")
    db.add_all([tomatoes, ana])
    db.commit()

    calls = [
        ("list_low_stock_items", {}),
        ("propose_reorder", {"inventory_item_id": tomatoes.id, "quantity": 20}),
        ("draft_purchase_order", {"supplier_id": supplier.id, "inventory_item_id": tomatoes.id, "quantity": 20}),
        ("propose_shift_change", {"staff_id": ana.id, "date_iso": "2026-10-09", "start_time_iso": "17:00:00",
                                  "end_time_iso": "22:00:00", "role": "Server"}),
    ]
    monkeypatch.setattr("app.agents.runner.get_provider", lambda: CrossAreaProvider(calls))
    run = crud.create_run(db, maestro.id, input="Get us ready for Friday.")

    execute_run(run.id, db=db)

    proposals = db.query(AgentAction).filter_by(run_id=run.id).all()
    assert sorted(p.action_type for p in proposals) == ["inventory_reorder", "purchase_order", "shift_change"]
    assert {p.agent_definition_id for p in proposals} == {maestro.id}
    assert db.query(AgentRun).count() == 1  # no child runs


def test_the_daily_stock_check_asks_the_agent_a_set_question(client, db, maestro, monkeypatch):
    from test_restaurant_setup import BASE, STEPS

    assert scheduler.stock_check_cron(db) is None  # nothing until setup is done
    for section, body in STEPS:
        client.put(f"{BASE}/{section}", json=body)
    client.post(f"{BASE}/complete")
    assert scheduler.stock_check_cron(db) == "30 7 * * *"

    submitted = []
    monkeypatch.setattr("app.agents.executor.submit", lambda run_id, db=None: submitted.append(run_id))
    run_id = scheduler.run_stock_check(db)

    run = db.get(AgentRun, run_id)
    assert submitted == [run_id]
    assert run.agent_definition_id == maestro.id
    assert run.trigger_type == "scheduled"
    assert run.input == scheduler.STOCK_CHECK_TASK

    # Turned off in settings, the job goes with it.
    client.put(f"{BASE}/inventory", json={"safety_days": 2, "default_lead_time_days": 4, "daily_stock_check": None})
    db.expire_all()
    assert scheduler.stock_check_cron(db) is None


def test_the_migration_merges_an_existing_team_into_maestro(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'merge.db'}"
    monkeypatch.setattr("app.config.settings.database_url", db_url)
    config = _alembic_config(db_url)
    command.upgrade(config, "c5a91e3f7d20")  # the last revision with six agents

    engine = create_engine(db_url)
    now = datetime(2026, 9, 1, tzinfo=timezone.utc).isoformat()
    team = {"boss": "id-boss", "inventory": "id-inv", "profit": "id-profit"}
    with engine.begin() as conn:
        for key, id_ in team.items():
            conn.execute(text(
                "INSERT INTO agent_definitions (id, key, name, description, role, system_prompt, model, "
                "tool_allowlist, schedule_cron, enabled, created_at) VALUES (:id, :key, :key, 'd', :role, 's', "
                "'m', '[]', :cron, 1, :now)"
            ), {"id": id_, "key": key, "role": "boss" if key == "boss" else "subagent",
                "cron": "0 9 * * *" if key == "boss" else None, "now": now})
        conn.execute(text(
            "INSERT INTO agent_runs (id, agent_definition_id, status, trigger_type, depth, created_at) "
            "VALUES ('run-1', 'id-inv', 'succeeded', 'manual', 0, :now)"
        ), {"now": now})
        conn.execute(text(
            "INSERT INTO agent_actions (id, run_id, agent_definition_id, action_type, payload, status, created_at) "
            "VALUES ('act-1', 'run-1', 'id-inv', 'inventory_reorder', '{}', 'pending', :now)"
        ), {"now": now})

    command.upgrade(config, "head")

    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, key, name, role, schedule_cron FROM agent_definitions")).all()
        assert [tuple(r) for r in rows] == [("id-boss", AGENT_KEY, "Assistant Manager", "main", "0 9 * * *")]
        assert conn.execute(text("SELECT agent_definition_id FROM agent_runs")).scalar() == "id-boss"
        assert conn.execute(text("SELECT agent_definition_id FROM agent_actions")).scalar() == "id-boss"

    # By name, not "-1": later migrations sit on top of this one.
    command.downgrade(config, "c5a91e3f7d20")
    with engine.connect() as conn:
        keys = {k for (k,) in conn.execute(text("SELECT key FROM agent_definitions"))}
    assert keys == {"boss", "inventory", "supply_chain", "employee_management", "profit", "marketing"}


def test_the_migration_leaves_an_empty_database_for_the_seed(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'empty.db'}"
    monkeypatch.setattr("app.config.settings.database_url", db_url)
    command.upgrade(_alembic_config(db_url), "head")
    with create_engine(db_url).connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM agent_definitions")).scalar() == 0


def test_scheduled_runs_still_default_to_their_schedule(db, maestro, monkeypatch):
    maestro.schedule_cron = "0 10 * * tue"
    db.commit()
    monkeypatch.setattr("app.agents.executor.submit", lambda run_id, db=None: None)
    run = db.get(AgentRun, scheduler.run_scheduled_agent(AGENT_KEY, db))
    assert run.input.startswith("Scheduled run (0 10 * * tue")

