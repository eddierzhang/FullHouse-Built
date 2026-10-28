"""Approving a proposal must actually change something -- or fail loudly."""

from datetime import date, time

import pytest

from app.agents.appliers import ApplyError, apply_action
from app.agents.models import AgentAction, AgentDefinition, AgentRun
from app.restaurant.models import InventoryItem, MenuItem, PurchaseOrder, Shift, Staff


@pytest.fixture
def proposal(db):
    """Builds a pending AgentAction attached to a real definition and run."""
    definition = AgentDefinition(
        key="inventory", name="Inventory", description="d", role="subagent", system_prompt="s"
    )
    db.add(definition)
    db.commit()
    run = AgentRun(agent_definition_id=definition.id, status="succeeded")
    db.add(run)
    db.commit()

    def _proposal(action_type: str, payload: dict) -> AgentAction:
        action = AgentAction(
            run_id=run.id,
            agent_definition_id=definition.id,
            action_type=action_type,
            payload=payload,
        )
        db.add(action)
        db.commit()
        return action

    return _proposal


@pytest.fixture
def stock(db):
    item = InventoryItem(name="Chicken Breast", unit="lb", quantity_on_hand=8.0, reorder_qty=20.0)
    db.add(item)
    db.commit()
    return item


def test_approving_a_reorder_places_an_order_and_receiving_it_restocks(client, db, proposal, stock, changes):
    action = proposal("inventory_reorder", {"inventory_item_id": stock.id, "quantity": 20})

    response = client.post(f"/api/v1/agents/actions/{action.id}/approve")
    assert response.status_code == 200

    # Approval places the order. The stock figure waits for the delivery.
    db.refresh(stock)
    assert stock.quantity_on_hand == 8.0
    assert response.json()["status"] == "approved"
    assert response.json()["applied_result"].startswith("Ordered 20 lb of Chicken Breast, expected ")
    assert "booked when it is received" in response.json()["applied_result"]
    order = db.query(PurchaseOrder).one()
    assert (order.status, order.quantity, order.unit, order.action_id) == ("ordered", 20, "lb", action.id)
    assert changes(entity_type="inventory_items", operation="update") == []
    assert changes(entity_type="purchase_orders", operation="insert")[-1].agent_action_id == action.id

    # It arrives: received on the Inventory page, booked once, with the order in the note.
    received = client.post(f"/api/v1/restaurant/purchase-orders/{order.id}/receive")
    assert received.status_code == 200, received.text
    assert received.json()["order"]["status"] == "received" and received.json()["item"]["quantity_on_hand"] == 28.0
    db.refresh(stock)
    assert stock.quantity_on_hand == 28.0
    update = changes(entity_type="inventory_items", operation="update")[-1]
    assert update.before == {"quantity_on_hand": 8.0}
    assert update.after == {"quantity_on_hand": 28.0}
    assert client.post(f"/api/v1/restaurant/purchase-orders/{order.id}/receive").status_code == 409


def test_approved_change_traces_back_to_the_proposing_run(client, db, proposal, stock, changes):
    action = proposal("inventory_reorder", {"inventory_item_id": stock.id, "quantity": 5})

    client.post(f"/api/v1/agents/actions/{action.id}/approve")

    placed = changes(entity_type="purchase_orders", operation="insert")[-1]
    # Placed by a human, but still attributable to the agent that proposed it.
    assert placed.actor_type == "human"
    assert placed.agent_run_id == action.run_id
    assert placed.agent_action_id == action.id

    # And so is the stock change when the order is received, by whoever receives it.
    order = db.query(PurchaseOrder).one()
    client.post(f"/api/v1/restaurant/purchase-orders/{order.id}/receive")
    update = changes(entity_type="inventory_items", operation="update")[-1]
    assert update.actor_type == "human"
    assert update.agent_run_id == action.run_id
    assert update.agent_action_id == action.id


def test_failed_apply_rolls_back_and_leaves_the_action_pending(client, db, proposal, stock):
    action = proposal(
        "menu_change", {"menu_item_id": "nope", "change_type": "promotion", "details": "2-for-1"}
    )

    response = client.post(f"/api/v1/agents/actions/{action.id}/approve")
    assert response.status_code == 422

    db.refresh(action)
    assert action.status == "pending"
    assert action.decided_at is None
    db.refresh(stock)
    assert stock.quantity_on_hand == 8.0


def test_missing_item_does_not_mark_the_action_approved(client, db, proposal):
    action = proposal("inventory_reorder", {"inventory_item_id": "ghost", "quantity": 5})

    response = client.post(f"/api/v1/agents/actions/{action.id}/approve")

    assert response.status_code == 422
    db.refresh(action)
    assert action.status == "pending"


def test_rejecting_applies_nothing(client, db, proposal, stock):
    action = proposal("inventory_reorder", {"inventory_item_id": stock.id, "quantity": 20})

    response = client.post(f"/api/v1/agents/actions/{action.id}/reject")

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    db.refresh(stock)
    assert stock.quantity_on_hand == 8.0


def test_an_action_cannot_be_approved_twice(client, proposal, stock):
    action = proposal("inventory_reorder", {"inventory_item_id": stock.id, "quantity": 1})

    assert client.post(f"/api/v1/agents/actions/{action.id}/approve").status_code == 200
    assert client.post(f"/api/v1/agents/actions/{action.id}/approve").status_code == 400


def test_shift_change_reaches_the_schedule(client, db, proposal):
    staff = Staff(name="Ana", role="server")
    db.add(staff)
    db.commit()
    action = proposal(
        "shift_change",
        {
            "staff_id": staff.id,
            "date": "2026-10-01",
            "start_time": "17:00",
            "end_time": "23:00",
            "role": "server",
        },
    )

    assert client.post(f"/api/v1/agents/actions/{action.id}/approve").status_code == 200

    shift = db.query(Shift).one()
    assert shift.staff_id == staff.id
    assert shift.date == date(2026, 10, 1)
    assert shift.start_time == time(17, 0)


def test_price_change_applies_the_structured_price(client, db, proposal):
    item = MenuItem(name="Carbonara", category="pasta", price=18.0)
    db.add(item)
    db.commit()
    action = proposal(
        "menu_change",
        {"menu_item_id": item.id, "change_type": "price_change", "details": "cut it", "new_price": 15.5},
    )

    assert client.post(f"/api/v1/agents/actions/{action.id}/approve").status_code == 200

    db.refresh(item)
    assert item.price == 15.5


def test_price_change_without_a_structured_price_is_refused(db, proposal):
    item = MenuItem(name="Carbonara", category="pasta", price=18.0)
    db.add(item)
    db.commit()
    action = proposal(
        "menu_change",
        {"menu_item_id": item.id, "change_type": "price_change", "details": "drop it to about $15"},
    )

    # Still refused -- but the operator can now supply the price at approval
    # time (see test_menu_management), so the message says so.
    with pytest.raises(ApplyError, match="needs a price"):
        apply_action(db, action)


def test_menu_removal_is_a_soft_delete(client, db, proposal):
    item = MenuItem(name="Tiramisu", category="dessert", price=9.0)
    db.add(item)
    db.commit()
    action = proposal("menu_change", {"menu_item_id": item.id, "change_type": "remove", "details": "slow"})

    assert client.post(f"/api/v1/agents/actions/{action.id}/approve").status_code == 200

    db.refresh(item)
    assert item.is_available is False
    assert db.query(MenuItem).count() == 1  # row survives for sales history


def test_unknown_action_type_is_refused(db, proposal):
    action = proposal("teleport_the_kitchen", {})

    with pytest.raises(ApplyError, match="No applier"):
        apply_action(db, action)


def test_negative_quantity_is_refused(db, proposal, stock):
    action = proposal("inventory_reorder", {"inventory_item_id": stock.id, "quantity": -5})

    with pytest.raises(ApplyError, match="positive"):
        apply_action(db, action)


def test_purchase_order_places_the_order_with_the_supplier_named(db, proposal, stock):
    action = proposal(
        "purchase_order",
        {"inventory_item_id": stock.id, "quantity": 12, "supplier_name": "Bay Foods"},
    )

    result = apply_action(db, action)

    assert result.startswith("Ordered 12 lb of Chicken Breast from Bay Foods, expected ")
    db.refresh(stock)
    assert stock.quantity_on_hand == 8.0
    order = db.query(PurchaseOrder).one()
    assert (order.supplier_name, order.quantity, order.status) == ("Bay Foods", 12, "ordered")
