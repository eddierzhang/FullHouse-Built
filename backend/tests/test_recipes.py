"""Recipes: the link between a dish and what it consumes."""

from datetime import datetime, timedelta, timezone

from app.restaurant.models import InventoryItem, MenuItem, Order, OrderItem, RecipeItem


def _ingredient(db, name="Flour", qty=100.0, cost=0.5, unit="lb"):
    item = InventoryItem(name=name, unit=unit, quantity_on_hand=qty, unit_cost=cost,
                         reorder_threshold=10.0, reorder_qty=50.0)
    db.add(item)
    db.commit()
    return item


def _dish(db, name="Pizza", price=14.0, cost=9.9):
    item = MenuItem(name=name, category="Entree", price=price, cost=cost)
    db.add(item)
    db.commit()
    return item


def _recipe(db, dish, *lines):
    for ingredient, quantity in lines:
        db.add(RecipeItem(menu_item_id=dish.id, inventory_item_id=ingredient.id, quantity=quantity))
    db.commit()


def test_a_recipe_prices_the_dish_from_its_ingredients(client, db):
    dish = _dish(db)
    flour = _ingredient(db, "Flour", cost=0.5)
    cheese = _ingredient(db, "Cheese", cost=4.0)
    _recipe(db, dish, (flour, 0.4), (cheese, 0.25))  # 0.20 + 1.00

    body = client.get(f"/api/v1/restaurant/menu-items/{dish.id}/recipe").json()

    assert body["ingredient_cost"] == 1.2
    assert body["margin_pct"] == 91.4
    assert [line["name"] for line in body["lines"]] == ["Cheese", "Flour"]  # costliest first


def test_recipe_cost_replaces_the_typed_in_cost(client, db):
    """The whole point: margins stop being an assertion."""
    dish = _dish(db, price=14.0, cost=9.9)  # a wildly wrong manual estimate
    flour = _ingredient(db, "Flour", cost=0.5)
    _recipe(db, dish, (flour, 2.0))  # really costs 1.00

    (row,) = [r for r in client.get("/api/v1/restaurant/menu-performance").json() if r["id"] == dish.id]

    assert row["cost"] == 1.0
    assert row["cost_source"] == "recipe"


def test_a_dish_without_a_recipe_keeps_its_manual_cost(client, db):
    dish = _dish(db, price=14.0, cost=4.2)

    (row,) = [r for r in client.get("/api/v1/restaurant/menu-performance").json() if r["id"] == dish.id]

    assert row["cost"] == 4.2
    assert row["cost_source"] == "manual"


def test_selling_a_dish_draws_down_its_ingredients(client, db):
    dish = _dish(db)
    flour = _ingredient(db, "Flour", qty=100.0)
    cheese = _ingredient(db, "Cheese", qty=20.0)
    _recipe(db, dish, (flour, 0.4), (cheese, 0.25))

    response = client.post(
        "/api/v1/restaurant/orders",
        json={"channel": "dine_in", "items": [{"menu_item_id": dish.id, "qty": 3}]},
    )

    assert response.status_code == 200
    db.refresh(flour)
    db.refresh(cheese)
    assert flour.quantity_on_hand == 98.8   # 100 - 3*0.4
    assert cheese.quantity_on_hand == 19.25  # 20 - 3*0.25


def test_depletion_is_recorded_in_the_change_log(client, db, changes):
    dish = _dish(db)
    flour = _ingredient(db, "Flour", qty=100.0)
    _recipe(db, dish, (flour, 2.0))

    client.post(
        "/api/v1/restaurant/orders",
        json={"channel": "dine_in", "items": [{"menu_item_id": dish.id, "qty": 1}]},
    )

    update = changes(entity_type="inventory_items", operation="update")[-1]
    assert update.before == {"quantity_on_hand": 100.0}
    assert update.after == {"quantity_on_hand": 98.0}


def test_an_order_that_would_oversell_is_refused(client, db):
    dish = _dish(db)
    flour = _ingredient(db, "Flour", qty=1.0)
    _recipe(db, dish, (flour, 2.0))

    response = client.post(
        "/api/v1/restaurant/orders",
        json={"channel": "dine_in", "items": [{"menu_item_id": dish.id, "qty": 1}]},
    )

    assert response.status_code == 409
    assert "Not enough stock" in response.json()["detail"]
    assert "Flour" in response.json()["detail"]
    db.refresh(flour)
    assert flour.quantity_on_hand == 1.0  # untouched
    assert db.query(Order).count() == 0   # and no order was recorded


def test_a_dish_with_no_recipe_still_sells(client, db):
    """Recipes are opt-in; an unlinked dish consumes nothing."""
    dish = _dish(db)

    response = client.post(
        "/api/v1/restaurant/orders",
        json={"channel": "dine_in", "items": [{"menu_item_id": dish.id, "qty": 2}]},
    )

    assert response.status_code == 200


def test_setting_a_recipe_replaces_the_previous_one(client, db):
    dish = _dish(db)
    flour = _ingredient(db, "Flour")
    cheese = _ingredient(db, "Cheese")
    _recipe(db, dish, (flour, 1.0))

    body = client.put(
        f"/api/v1/restaurant/menu-items/{dish.id}/recipe",
        json={"lines": [{"inventory_item_id": cheese.id, "quantity": 0.5}]},
    ).json()

    assert [line["name"] for line in body["lines"]] == ["Cheese"]
    assert db.query(RecipeItem).count() == 1


def test_a_recipe_can_be_cleared(client, db):
    dish = _dish(db)
    flour = _ingredient(db, "Flour")
    _recipe(db, dish, (flour, 1.0))

    body = client.put(f"/api/v1/restaurant/menu-items/{dish.id}/recipe", json={"lines": []}).json()

    assert body["lines"] == []
    assert body["ingredient_cost"] == 0


def test_duplicate_ingredients_are_rejected(client, db):
    dish = _dish(db)
    flour = _ingredient(db, "Flour")

    response = client.put(
        f"/api/v1/restaurant/menu-items/{dish.id}/recipe",
        json={"lines": [
            {"inventory_item_id": flour.id, "quantity": 1.0},
            {"inventory_item_id": flour.id, "quantity": 2.0},
        ]},
    )

    assert response.status_code == 422
    assert "twice" in response.json()["detail"]


def test_a_zero_quantity_ingredient_is_rejected(client, db):
    dish = _dish(db)
    flour = _ingredient(db, "Flour")

    response = client.put(
        f"/api/v1/restaurant/menu-items/{dish.id}/recipe",
        json={"lines": [{"inventory_item_id": flour.id, "quantity": 0}]},
    )

    assert response.status_code == 422


def test_an_unknown_ingredient_is_rejected(client, db):
    dish = _dish(db)

    response = client.put(
        f"/api/v1/restaurant/menu-items/{dish.id}/recipe",
        json={"lines": [{"inventory_item_id": "ghost", "quantity": 1}]},
    )

    assert response.status_code == 404


def test_days_of_cover_is_computed_from_real_consumption(client, db):
    """The metric I refused to fake before recipes existed."""
    dish = _dish(db)
    flour = _ingredient(db, "Flour", qty=60.0)
    _recipe(db, dish, (flour, 2.0))

    # 30 units sold over the window -> 60 flour -> 2/day against 30 days.
    order = Order(status="completed", total=1.0, created_at=datetime.now(timezone.utc) - timedelta(days=1))
    order.items.append(OrderItem(menu_item_id=dish.id, qty=30, unit_price=14.0))
    db.add(order)
    db.commit()

    body = client.get("/api/v1/restaurant/supply-chain").json()
    (cover,) = [c for c in body["cover"] if c["id"] == flour.id]

    assert cover["daily_use"] == 2.0
    assert cover["days_of_cover"] == 30.0


def test_cover_is_unknown_rather_than_zero_without_consumption(client, db):
    flour = _ingredient(db, "Flour", qty=60.0)

    body = client.get("/api/v1/restaurant/supply-chain").json()
    (cover,) = [c for c in body["cover"] if c["id"] == flour.id]

    assert cover["daily_use"] is None
    assert cover["days_of_cover"] is None
    assert cover["at_risk"] is False


def test_an_ingredient_running_out_before_resupply_is_flagged(client, db):
    from app.restaurant.models import Supplier

    supplier = Supplier(name="Slow Co", lead_time_days=7)
    db.add(supplier)
    db.commit()
    dish = _dish(db)
    flour = _ingredient(db, "Flour", qty=6.0)
    flour.supplier_id = supplier.id
    db.add(flour)
    db.commit()
    _recipe(db, dish, (flour, 2.0))

    order = Order(status="completed", total=1.0, created_at=datetime.now(timezone.utc) - timedelta(days=1))
    order.items.append(OrderItem(menu_item_id=dish.id, qty=30, unit_price=14.0))
    db.add(order)
    db.commit()

    body = client.get("/api/v1/restaurant/supply-chain").json()
    (cover,) = [c for c in body["cover"] if c["id"] == flour.id]

    assert cover["days_of_cover"] == 3.0  # 6 on hand / 2 per day
    assert cover["lead_time_days"] == 7
    assert cover["at_risk"] is True       # gone before a delivery could land
    assert body["at_risk_count"] == 1
