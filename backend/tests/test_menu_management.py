"""Menu editing and the sales figures behind the marketing page."""

from app.restaurant.models import MenuItem, Order, OrderItem


def _item(db, name="Margherita", price=14.0, cost=4.2, **kw):
    item = MenuItem(name=name, category=kw.pop("category", "pizza"), price=price, cost=cost, **kw)
    db.add(item)
    db.commit()
    return item


def _sell(db, item, qty=3, status="completed"):
    order = Order(status=status, total=item.price * qty)
    order.items.append(OrderItem(menu_item_id=item.id, qty=qty, unit_price=item.price))
    db.add(order)
    db.commit()
    return order


def test_performance_reports_units_revenue_and_profit(client, db):
    item = _item(db)
    _sell(db, item, qty=3)

    (row,) = [r for r in client.get("/api/v1/restaurant/menu-performance").json() if r["id"] == item.id]

    assert row["units_sold"] == 3
    assert row["revenue"] == 42.0
    assert row["margin_pct"] == 70.0
    assert row["profit"] == 29.4  # (14.00 - 4.20) * 3


def test_unsold_items_appear_with_zeroes(client, db):
    _item(db, name="Never Ordered")

    (row,) = [r for r in client.get("/api/v1/restaurant/menu-performance").json() if r["name"] == "Never Ordered"]

    assert row["units_sold"] == 0
    assert row["revenue"] == 0
    assert row["margin_pct"] > 0  # margin is knowable without a single sale


def test_incomplete_orders_do_not_count_as_sales(client, db):
    item = _item(db)
    _sell(db, item, qty=5, status="cancelled")

    (row,) = [r for r in client.get("/api/v1/restaurant/menu-performance").json() if r["id"] == item.id]

    assert row["units_sold"] == 0


def test_performance_is_ranked_by_units_sold(client, db):
    quiet = _item(db, name="Quiet Dish")
    popular = _item(db, name="Popular Dish")
    _sell(db, popular, qty=10)
    _sell(db, quiet, qty=1)

    rows = client.get("/api/v1/restaurant/menu-performance").json()

    assert [r["name"] for r in rows[:2]] == ["Popular Dish", "Quiet Dish"]


def test_price_can_be_changed(client, db):
    item = _item(db)

    response = client.patch(f"/api/v1/restaurant/menu-items/{item.id}", json={"price": 16.5})

    assert response.status_code == 200
    db.refresh(item)
    assert item.price == 16.5
    assert item.name == "Margherita"  # untouched


def test_price_change_is_recorded_with_both_values(client, db, changes):
    item = _item(db)

    client.patch(
        f"/api/v1/restaurant/menu-items/{item.id}",
        json={"price": 16.5},
        headers={"X-Actor-Id": "eddie"},
    )

    update = changes(entity_type="menu_items", operation="update")[-1]
    assert update.before == {"price": 14.0}
    assert update.after == {"price": 16.5}
    assert update.actor_id == "eddie"


def test_negative_price_is_rejected(client, db):
    item = _item(db)

    assert client.patch(f"/api/v1/restaurant/menu-items/{item.id}", json={"price": -1}).status_code == 422
    db.refresh(item)
    assert item.price == 14.0


def test_taking_an_item_off_the_menu(client, db):
    item = _item(db)

    client.patch(f"/api/v1/restaurant/menu-items/{item.id}", json={"is_available": False})

    db.refresh(item)
    assert item.is_available is False
    # Still reported, so its past contribution stays visible.
    assert any(r["id"] == item.id for r in client.get("/api/v1/restaurant/menu-performance").json())


def test_an_unsold_item_can_be_deleted(client, db):
    item = _item(db, name="Mistake")

    assert client.delete(f"/api/v1/restaurant/menu-items/{item.id}").status_code == 204
    assert db.get(MenuItem, item.id) is None


def test_a_sold_item_cannot_be_deleted(client, db):
    item = _item(db)
    _sell(db, item)

    response = client.delete(f"/api/v1/restaurant/menu-items/{item.id}")

    assert response.status_code == 409
    assert "Mark it unavailable instead" in response.json()["detail"]
    assert db.get(MenuItem, item.id) is not None


def test_approving_an_agent_price_change_moves_the_price(client, db):
    """The marketing agent's proposal, applied end to end."""
    from app.agents.models import AgentAction, AgentDefinition, AgentRun

    item = _item(db)
    definition = AgentDefinition(
        key="marketing", name="Marketing", description="d", role="subagent", system_prompt="s"
    )
    db.add(definition)
    db.commit()
    run = AgentRun(agent_definition_id=definition.id, status="succeeded")
    db.add(run)
    db.commit()
    action = AgentAction(
        run_id=run.id,
        agent_definition_id=definition.id,
        action_type="menu_change",
        payload={"menu_item_id": item.id, "change_type": "price_change", "details": "promo", "new_price": 11.0},
    )
    db.add(action)
    db.commit()

    assert client.post(f"/api/v1/agents/actions/{action.id}/approve").status_code == 200

    db.refresh(item)
    assert item.price == 11.0


def _proposal(db, change_type, new_price=None, item=None, details="promo"):
    from app.agents.models import AgentAction, AgentDefinition, AgentRun

    item = item or _item(db)
    definition = AgentDefinition(
        key="marketing", name="Marketing", description="d", role="subagent", system_prompt="s"
    )
    db.add(definition)
    db.commit()
    run = AgentRun(agent_definition_id=definition.id, status="succeeded")
    db.add(run)
    db.commit()
    action = AgentAction(
        run_id=run.id,
        agent_definition_id=definition.id,
        action_type="menu_change",
        payload={
            "menu_item_id": item.id,
            "change_type": change_type,
            "details": details,
            "new_price": new_price,
        },
    )
    db.add(action)
    db.commit()
    return action, item


def test_a_promotion_with_a_price_applies(client, db):
    action, item = _proposal(db, "promotion", new_price=9.0)

    response = client.post(f"/api/v1/agents/actions/{action.id}/approve")

    assert response.status_code == 200
    assert "Promotion applied" in response.json()["applied_result"]
    db.refresh(item)
    assert item.price == 9.0


def test_a_promotion_without_a_price_can_be_approved_with_one(client, db):
    """The BOGO case: the proposal names no price, the operator does."""
    action, item = _proposal(db, "promotion", details="Buy 1 Get 1 Free")

    response = client.post(
        f"/api/v1/agents/actions/{action.id}/approve", json={"overrides": {"new_price": 7.0}}
    )

    assert response.status_code == 200
    db.refresh(item)
    assert item.price == 7.0
    # The record shows what was actually approved, not the incomplete proposal.
    assert action.payload["new_price"] == 7.0


def test_a_promotion_without_a_price_still_refuses_a_bare_approve(client, db):
    action, item = _proposal(db, "promotion", details="Buy 1 Get 1 Free")

    response = client.post(f"/api/v1/agents/actions/{action.id}/approve")

    assert response.status_code == 422
    assert "needs a price" in response.json()["detail"]
    db.refresh(action)
    assert action.status == "pending"
    db.refresh(item)
    assert item.price == 14.0


def test_the_queue_says_what_an_action_still_needs(client, db):
    _proposal(db, "promotion", details="Buy 1 Get 1 Free")

    (listed,) = client.get("/api/v1/agents/actions", params={"status": "pending"}).json()

    assert listed["appliable"] is True
    assert listed["needs_input"] == ["new_price"]


def test_a_complete_proposal_needs_nothing(client, db):
    _proposal(db, "promotion", new_price=9.0)

    (listed,) = client.get("/api/v1/agents/actions", params={"status": "pending"}).json()

    assert listed["needs_input"] == []


def test_an_unknown_action_type_is_flagged_as_not_appliable(client, db):
    from app.agents.models import AgentAction, AgentDefinition, AgentRun

    definition = AgentDefinition(key="x", name="X", description="d", role="subagent", system_prompt="s")
    db.add(definition)
    db.commit()
    run = AgentRun(agent_definition_id=definition.id, status="succeeded")
    db.add(run)
    db.commit()
    db.add(AgentAction(run_id=run.id, agent_definition_id=definition.id,
                       action_type="teleport_kitchen", payload={}))
    db.commit()

    (listed,) = client.get("/api/v1/agents/actions", params={"status": "pending"}).json()

    assert listed["appliable"] is False


def test_a_negative_override_price_is_refused(client, db):
    action, item = _proposal(db, "promotion")

    response = client.post(
        f"/api/v1/agents/actions/{action.id}/approve", json={"overrides": {"new_price": -2}}
    )

    assert response.status_code == 422
    db.refresh(item)
    assert item.price == 14.0
