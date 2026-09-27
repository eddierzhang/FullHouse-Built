"""Approving a proposal must actually change something -- or fail loudly."""

from datetime import date, time

import pytest

from app.agents.appliers import ApplyError, apply_action
from app.agents.models import AgentAction, AgentDefinition, AgentRun
from app.restaurant.models import InventoryItem, MenuItem, Shift, Staff


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


def test_approving_a_reorder_restocks(client, db, proposal, stock, changes):
    action = proposal("inventory_reorder", {"inventory_item_id": stock.id, "quantity": 20})

    response = client.post(f"/api/v1/agents/actions/{action.id}/approve")
    assert response.status_code == 200

    db.refresh(stock)
    assert stock.quantity_on_hand == 28.0
    assert response.json()["status"] == "approved"
    assert "8 -> 28" in response.json()["applied_result"]

    update = changes(entity_type="inventory_items", operation="update")[-1]
    assert update.before == {"quantity_on_hand": 8.0}
    assert update.after == {"quantity_on_hand": 28.0}


def test_approved_change_traces_back_to_the_proposing_run(client, db, proposal, stock, changes):
    action = proposal("inventory_reorder", {"inventory_item_id": stock.id, "quantity": 5})

    client.post(f"/api/v1/agents/actions/{action.id}/approve")

    update = changes(entity_type="inventory_items", operation="update")[-1]
    # Applied by a human, but still attributable to the agent that proposed it.
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


def test_purchase_order_restocks_and_names_the_supplier(db, proposal, stock):
    action = proposal(
        "purchase_order",
        {"inventory_item_id": stock.id, "quantity": 12, "supplier_name": "Bay Foods"},
    )

    result = apply_action(db, action)

    assert "Bay Foods" in result
    assert stock.quantity_on_hand == 20.0
