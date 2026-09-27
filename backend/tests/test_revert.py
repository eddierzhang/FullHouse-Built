"""Reverting: the payoff for storing both sides of every change."""

from datetime import date, time

from app.restaurant.models import InventoryItem, MenuItem, Shift, Staff


def _latest_change_id(client, **params) -> int:
    return client.get("/api/v1/changes", params=params).json()["items"][0]["id"]


def test_revert_an_update_restores_the_prior_value(client, db):
    item = InventoryItem(name="Tomatoes", unit="lb", quantity_on_hand=10.0)
    db.add(item)
    db.commit()
    item.quantity_on_hand = 99.0
    db.commit()
    change_id = _latest_change_id(client, operation="update")

    response = client.post(f"/api/v1/changes/{change_id}/revert")

    assert response.status_code == 200
    db.expire_all()
    assert item.quantity_on_hand == 10.0


def test_revert_is_itself_recorded(client, db):
    item = InventoryItem(name="Tomatoes", unit="lb", quantity_on_hand=10.0)
    db.add(item)
    db.commit()
    item.quantity_on_hand = 99.0
    db.commit()
    change_id = _latest_change_id(client, operation="update")

    body = client.post(f"/api/v1/changes/{change_id}/revert").json()

    assert body["resulting_change_ids"]
    undo = client.get(f"/api/v1/changes/{body['resulting_change_ids'][0]}").json()
    assert undo["note"] == f"Revert of change {change_id}"
    assert undo["before"] == {"quantity_on_hand": 99.0}
    assert undo["after"] == {"quantity_on_hand": 10.0}
    # The original is still there: history is appended to, never rewritten.
    assert client.get(f"/api/v1/changes/{change_id}").status_code == 200


def test_revert_carries_a_custom_note(client, db):
    item = MenuItem(name="Carbonara", category="pasta", price=18.0)
    db.add(item)
    db.commit()
    item.price = 25.0
    db.commit()
    change_id = _latest_change_id(client, operation="update")

    body = client.post(
        f"/api/v1/changes/{change_id}/revert", json={"note": "priced out of market"}
    ).json()

    undo = client.get(f"/api/v1/changes/{body['resulting_change_ids'][0]}").json()
    assert undo["note"] == "priced out of market"


def test_revert_an_insert_deletes_the_row(client, db):
    item = MenuItem(name="Mistake", category="pizza", price=5.0)
    db.add(item)
    db.commit()
    item_id = item.id
    change_id = _latest_change_id(client, operation="insert")

    response = client.post(f"/api/v1/changes/{change_id}/revert")

    assert response.status_code == 200
    assert db.get(MenuItem, item_id) is None


def test_revert_a_delete_recreates_the_row_with_its_original_key(client, db):
    item = MenuItem(name="Tiramisu", category="dessert", price=9.0, description="classic")
    db.add(item)
    db.commit()
    item_id = item.id
    db.delete(item)
    db.commit()
    change_id = _latest_change_id(client, operation="delete")

    response = client.post(f"/api/v1/changes/{change_id}/revert")

    assert response.status_code == 200
    restored = db.get(MenuItem, item_id)
    assert restored is not None
    assert restored.name == "Tiramisu"
    assert restored.description == "classic"


def test_revert_restores_dates_as_dates_not_strings(client, db):
    """The log stores temporal values as ISO strings; revert must coerce back."""
    staff = Staff(name="Ana", role="server")
    db.add(staff)
    db.commit()
    shift = Shift(
        staff_id=staff.id, date=date(2026, 10, 1), start_time=time(17, 0), end_time=time(23, 0),
        role="server",
    )
    db.add(shift)
    db.commit()
    shift_id = shift.id
    db.delete(shift)
    db.commit()
    change_id = _latest_change_id(client, operation="delete", entity_type="shifts")

    assert client.post(f"/api/v1/changes/{change_id}/revert").status_code == 200

    restored = db.get(Shift, shift_id)
    assert restored.date == date(2026, 10, 1)  # not "2026-10-01"
    assert restored.start_time == time(17, 0)


def test_revert_refuses_when_the_entity_moved_on(client, db):
    item = InventoryItem(name="Tomatoes", unit="lb", quantity_on_hand=10.0)
    db.add(item)
    db.commit()
    item.quantity_on_hand = 50.0
    db.commit()
    change_id = _latest_change_id(client, operation="update")
    item.quantity_on_hand = 77.0  # a newer edit the revert would clobber
    db.commit()

    response = client.post(f"/api/v1/changes/{change_id}/revert")

    assert response.status_code == 409
    assert "changed since" in response.json()["detail"]
    db.expire_all()
    assert item.quantity_on_hand == 77.0


def test_force_overrides_the_conflict(client, db):
    item = InventoryItem(name="Tomatoes", unit="lb", quantity_on_hand=10.0)
    db.add(item)
    db.commit()
    item.quantity_on_hand = 50.0
    db.commit()
    change_id = _latest_change_id(client, operation="update")
    item.quantity_on_hand = 77.0
    db.commit()

    response = client.post(f"/api/v1/changes/{change_id}/revert", json={"force": True})

    assert response.status_code == 200
    db.expire_all()
    assert item.quantity_on_hand == 10.0


def test_reverting_twice_conflicts(client, db):
    item = InventoryItem(name="Tomatoes", unit="lb", quantity_on_hand=10.0)
    db.add(item)
    db.commit()
    item.quantity_on_hand = 99.0
    db.commit()
    change_id = _latest_change_id(client, operation="update")

    assert client.post(f"/api/v1/changes/{change_id}/revert").status_code == 200
    # The second attempt sees state that no longer matches the change's `after`.
    assert client.post(f"/api/v1/changes/{change_id}/revert").status_code == 409


def test_revert_of_a_missing_change_is_404(client, db):
    assert client.post("/api/v1/changes/99999/revert").status_code == 404


def test_revert_of_a_vanished_entity_conflicts(client, db):
    item = MenuItem(name="Gone", category="pizza", price=5.0)
    db.add(item)
    db.commit()
    item.price = 6.0
    db.commit()
    change_id = _latest_change_id(client, operation="update")
    db.delete(item)
    db.commit()

    response = client.post(f"/api/v1/changes/{change_id}/revert")

    assert response.status_code == 409
    assert "no longer exists" in response.json()["detail"]


def test_reverting_an_approved_reorder_undoes_the_restock(client, db):
    """The end-to-end case: an agent's approved proposal, undone."""
    from app.agents.models import AgentAction, AgentDefinition, AgentRun

    item = InventoryItem(name="Chicken", unit="lb", quantity_on_hand=8.0)
    definition = AgentDefinition(
        key="inventory", name="Inv", description="d", role="subagent", system_prompt="s"
    )
    db.add_all([item, definition])
    db.commit()
    run = AgentRun(agent_definition_id=definition.id, status="succeeded")
    db.add(run)
    db.commit()
    action = AgentAction(
        run_id=run.id, agent_definition_id=definition.id, action_type="inventory_reorder",
        payload={"inventory_item_id": item.id, "quantity": 25},
    )
    db.add(action)
    db.commit()

    assert client.post(f"/api/v1/agents/actions/{action.id}/approve").status_code == 200
    db.expire_all()
    assert item.quantity_on_hand == 33.0

    change_id = _latest_change_id(client, entity_type="inventory_items", operation="update")
    assert client.post(f"/api/v1/changes/{change_id}/revert").status_code == 200

    db.expire_all()
    assert item.quantity_on_hand == 8.0
    # The action stays approved -- reverting the effect is not the same as
    # un-approving the decision, and the log shows both steps.
    db.refresh(action)
    assert action.status == "approved"


def test_revert_is_refused_when_it_would_orphan_a_reference(client, db):
    """Undoing a menu item's creation must not strand the orders that sold it."""
    from app.restaurant.models import Order, OrderItem

    item = MenuItem(name="Pizza", category="pizza", price=10.0)
    db.add(item)
    db.commit()
    change_id = _latest_change_id(client, operation="insert", entity_type="menu_items")

    order = Order(status="completed")
    order.items.append(OrderItem(menu_item_id=item.id, qty=2, unit_price=10.0))
    db.add(order)
    db.commit()

    response = client.post(f"/api/v1/changes/{change_id}/revert")

    assert response.status_code == 409
    assert db.get(MenuItem, item.id) is not None
    assert db.query(OrderItem).one().menu_item_id == item.id
