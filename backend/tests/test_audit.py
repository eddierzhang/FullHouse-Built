"""The change log captures mutations regardless of which path made them."""

from app.agents.models import AgentDefinition, AgentEvent, AgentRun
from app.audit.context import AGENT, Actor, actor_context
from app.audit.models import ChangeLog
from app.restaurant.models import InventoryItem, MenuItem


def _item(db, **overrides) -> InventoryItem:
    item = InventoryItem(
        **{"name": "Tomatoes", "unit": "lb", "quantity_on_hand": 10.0, "unit_cost": 2.5, **overrides}
    )
    db.add(item)
    db.commit()
    return item


def _run(db) -> AgentRun:
    """A real definition + run -- foreign keys are enforced."""
    definition = AgentDefinition(
        key="inventory", name="Inv", description="d", role="subagent", system_prompt="s"
    )
    db.add(definition)
    db.commit()
    run = AgentRun(agent_definition_id=definition.id, status="running")
    db.add(run)
    db.commit()
    return run


def test_insert_is_recorded_with_full_snapshot(db, changes):
    item = _item(db)

    (row,) = changes(entity_type="inventory_items")
    assert row.operation == "insert"
    assert row.entity_id == item.id
    assert row.before is None
    assert row.after["name"] == "Tomatoes"
    assert row.after["quantity_on_hand"] == 10.0


def test_update_records_only_changed_columns(db, changes):
    item = _item(db)

    item.quantity_on_hand = 42.0
    db.commit()

    update = changes(entity_type="inventory_items", operation="update")[-1]
    assert update.changed_fields == ["quantity_on_hand"]
    assert update.before == {"quantity_on_hand": 10.0}
    assert update.after == {"quantity_on_hand": 42.0}
    # Untouched columns stay out of the diff.
    assert "name" not in update.after


def test_delete_records_the_final_state(db, changes):
    item = _item(db)
    item_id = item.id

    db.delete(item)
    db.commit()

    delete = changes(entity_type="inventory_items", operation="delete")[-1]
    assert delete.entity_id == item_id
    assert delete.after is None
    assert delete.before["name"] == "Tomatoes"


def test_no_op_save_writes_nothing(db, changes):
    item = _item(db)
    before_count = len(changes())

    item.quantity_on_hand = item.quantity_on_hand  # same value
    db.add(item)
    db.commit()

    assert len(changes()) == before_count


def test_change_log_does_not_audit_itself(db, changes):
    _item(db)
    # One insert row, and no audit row describing that audit row.
    assert changes(entity_type="change_log") == []
    assert db.query(ChangeLog).count() == 1


def test_agent_events_are_excluded(db, changes):
    run = _run(db)

    db.add(AgentEvent(run_id=run.id, seq=1, type="log", payload={"text": "hi"}))
    db.commit()

    # The run itself is tracked; its append-only event log is not.
    assert changes(entity_type="agent_runs") != []
    assert changes(entity_type="agent_events") == []


def test_actor_context_attributes_the_change(db, changes):
    with actor_context(Actor(type=AGENT, id="inventory", agent_run_id="run-123")):
        _item(db)

    (row,) = changes(entity_type="inventory_items")
    assert row.actor_type == AGENT
    assert row.actor_id == "inventory"
    assert row.agent_run_id == "run-123"


def test_actor_defaults_to_system_outside_any_context(db, changes):
    _item(db)

    (row,) = changes(entity_type="inventory_items")
    assert row.actor_type == "system"


def test_nested_actor_context_unwinds(db, changes):
    with actor_context(Actor(type=AGENT, id="boss", agent_run_id="run-boss")):
        with actor_context(Actor(type=AGENT, id="inventory", agent_run_id="run-child")):
            _item(db, name="Child item")
        _item(db, name="Boss item")

    rows = {row.after["name"]: row for row in changes(entity_type="inventory_items")}
    assert rows["Child item"].agent_run_id == "run-child"
    assert rows["Boss item"].agent_run_id == "run-boss"


def test_http_mutation_is_attributed_to_the_caller(client, changes):
    response = client.post(
        "/api/v1/restaurant/menu-items",
        json={"name": "Margherita", "category": "pizza", "price": 12.0, "cost": 4.0},
        headers={"X-Actor-Id": "eddie"},
    )
    assert response.status_code == 200

    (row,) = changes(entity_type="menu_items")
    assert row.operation == "insert"
    assert row.actor_type == "human"
    assert row.actor_id == "eddie"


def test_http_mutation_without_actor_header_is_anonymous(client, changes):
    client.post(
        "/api/v1/restaurant/menu-items",
        json={"name": "Calzone", "category": "pizza", "price": 14.0, "cost": 5.0},
    )

    (row,) = changes(entity_type="menu_items")
    assert row.actor_id == "anonymous"


def test_datetimes_are_serialised(db, changes):
    """A JSON column can't take a raw datetime; the snapshot must coerce it."""
    _run(db)

    (row,) = changes(entity_type="agent_runs")
    assert isinstance(row.after["created_at"], str)


def test_multiple_entities_in_one_flush_each_get_a_row(db, changes):
    db.add(MenuItem(name="Soup", category="starter", price=6.0))
    db.add(MenuItem(name="Salad", category="starter", price=7.0))
    db.commit()

    assert len(changes(entity_type="menu_items")) == 2


def test_active_history_is_enabled_on_tracked_columns():
    """Guards the wiring that makes `before` values recoverable at all.

    Without it, a column assigned after a commit expired it records its
    old value as None -- silently, and only for that access pattern.
    """
    from sqlalchemy import inspect as sa_inspect

    from app.audit.models import ChangeLog

    mapper = sa_inspect(InventoryItem)
    assert all(
        mapper.class_manager[attr.key].impl.active_history for attr in mapper.column_attrs
    )

    # ...and is left alone on the log's own table, which is never diffed.
    # Primary keys are excluded from the check: SQLAlchemy turns
    # active_history on for those itself, regardless of this wiring.
    log_mapper = sa_inspect(ChangeLog)
    assert not any(
        log_mapper.class_manager[attr.key].impl.active_history
        for attr in log_mapper.column_attrs
        if not attr.columns[0].primary_key
    )
