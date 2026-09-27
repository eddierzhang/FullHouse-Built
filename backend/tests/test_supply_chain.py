"""Supplier exposure, and editing the things it is computed from."""

from app.restaurant.models import InventoryItem, Supplier


def _supplier(db, name="Sysco", lead=2):
    supplier = Supplier(name=name, contact_info=f"{name.lower()}@example.com", lead_time_days=lead)
    db.add(supplier)
    db.commit()
    return supplier


def _item(db, supplier=None, name="Chicken", qty=50.0, threshold=15.0, reorder=40.0, cost=3.0):
    item = InventoryItem(
        name=name,
        unit="lb",
        quantity_on_hand=qty,
        reorder_threshold=threshold,
        reorder_qty=reorder,
        unit_cost=cost,
        supplier_id=supplier.id if supplier else None,
    )
    db.add(item)
    db.commit()
    return item


def test_stock_value_is_grouped_by_supplier(client, db):
    a = _supplier(db, "Sysco")
    b = _supplier(db, "Green Valley", lead=1)
    _item(db, a, name="Chicken", qty=10, cost=3.0)   # 30
    _item(db, a, name="Flour", qty=100, cost=0.5)    # 50
    _item(db, b, name="Lettuce", qty=20, cost=1.0)   # 20

    body = client.get("/api/v1/restaurant/supply-chain").json()

    rows = {s["name"]: s for s in body["suppliers"]}
    assert rows["Sysco"]["stock_value"] == 80.0
    assert rows["Sysco"]["item_count"] == 2
    assert rows["Green Valley"]["stock_value"] == 20.0
    assert body["total_stock_value"] == 100.0


def test_concentration_share_is_reported(client, db):
    a = _supplier(db, "Dominant")
    b = _supplier(db, "Minor")
    _item(db, a, name="Big", qty=90, cost=1.0)
    _item(db, b, name="Small", qty=10, cost=1.0)

    body = client.get("/api/v1/restaurant/supply-chain").json()

    assert body["suppliers"][0]["name"] == "Dominant"  # ranked by value
    assert body["suppliers"][0]["share_pct"] == 90.0
    assert body["suppliers"][1]["share_pct"] == 10.0


def test_restock_cost_prices_the_reorder_not_the_shelf(client, db):
    supplier = _supplier(db)
    _item(db, supplier, qty=5, threshold=15, reorder=40, cost=3.0)

    body = client.get("/api/v1/restaurant/supply-chain").json()

    assert body["low_stock_count"] == 1
    assert body["restock_cost"] == 120.0  # 40 to order x $3, not 5 on hand
    assert body["low_stock"][0]["lead_time_days"] == 2


def test_healthy_stock_produces_no_exposure(client, db):
    supplier = _supplier(db)
    _item(db, supplier, qty=100, threshold=10)

    body = client.get("/api/v1/restaurant/supply-chain").json()

    assert body["low_stock"] == []
    assert body["restock_cost"] == 0
    assert body["longest_lead_days"] == 0


def test_items_with_no_supplier_are_surfaced(client, db):
    _item(db, None, name="Orphan", qty=5, threshold=10, cost=2.0)

    body = client.get("/api/v1/restaurant/supply-chain").json()

    assert body["unassigned_item_count"] == 1
    assert body["unassigned_stock_value"] == 10.0
    # It is low and has nobody to order from -- lead time is unknowable, not zero.
    assert body["low_stock"][0]["supplier_name"] is None
    assert body["low_stock"][0]["lead_time_days"] is None


def test_low_items_are_ordered_by_lead_time_worst_first(client, db):
    slow = _supplier(db, "Slow", lead=7)
    fast = _supplier(db, "Fast", lead=1)
    _item(db, fast, name="Quick", qty=1, threshold=10)
    _item(db, slow, name="Slow item", qty=1, threshold=10)

    body = client.get("/api/v1/restaurant/supply-chain").json()

    assert [r["name"] for r in body["low_stock"]] == ["Slow item", "Quick"]
    assert body["longest_lead_days"] == 7


def test_lead_time_can_be_corrected(client, db):
    supplier = _supplier(db, lead=2)

    response = client.patch(
        f"/api/v1/restaurant/suppliers/{supplier.id}", json={"lead_time_days": 5}
    )

    assert response.status_code == 200
    db.refresh(supplier)
    assert supplier.lead_time_days == 5


def test_negative_lead_time_is_rejected(client, db):
    supplier = _supplier(db)

    assert client.patch(
        f"/api/v1/restaurant/suppliers/{supplier.id}", json={"lead_time_days": -1}
    ).status_code == 422


def test_supplier_with_items_cannot_be_deleted(client, db):
    supplier = _supplier(db)
    _item(db, supplier)

    response = client.delete(f"/api/v1/restaurant/suppliers/{supplier.id}")

    assert response.status_code == 409
    assert "still supplies 1 item" in response.json()["detail"]


def test_supplier_can_be_deleted_once_nothing_references_it(client, db):
    supplier = _supplier(db)

    assert client.delete(f"/api/v1/restaurant/suppliers/{supplier.id}").status_code == 204
    assert db.get(Supplier, supplier.id) is None


def test_an_item_can_be_reassigned_to_another_supplier(client, db):
    a = _supplier(db, "Old")
    b = _supplier(db, "New")
    item = _item(db, a)

    response = client.patch(
        f"/api/v1/restaurant/inventory-items/{item.id}", json={"supplier_id": b.id}
    )

    assert response.status_code == 200
    db.refresh(item)
    assert item.supplier_id == b.id


def test_reassigning_to_an_unknown_supplier_is_404(client, db):
    supplier = _supplier(db)
    item = _item(db, supplier)

    assert client.patch(
        f"/api/v1/restaurant/inventory-items/{item.id}", json={"supplier_id": "ghost"}
    ).status_code == 404


def test_stock_adjustment_is_recorded_with_both_values(client, db, changes):
    supplier = _supplier(db)
    item = _item(db, supplier, qty=50.0)

    client.patch(
        f"/api/v1/restaurant/inventory-items/{item.id}",
        json={"quantity_on_hand": 8.0},
        headers={"X-Actor-Id": "eddie"},
    )

    update = changes(entity_type="inventory_items", operation="update")[-1]
    assert update.before == {"quantity_on_hand": 50.0}
    assert update.after == {"quantity_on_hand": 8.0}
    assert update.actor_id == "eddie"


def test_negative_stock_is_rejected(client, db):
    supplier = _supplier(db)
    item = _item(db, supplier)

    response = client.patch(
        f"/api/v1/restaurant/inventory-items/{item.id}", json={"quantity_on_hand": -5}
    )

    assert response.status_code == 422
    assert "quantity on hand cannot be negative" in response.json()["detail"]
