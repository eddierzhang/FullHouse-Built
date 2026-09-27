"""The numbers behind the profit page."""

from datetime import datetime, timedelta, timezone

from app.restaurant.models import MenuItem, Order, OrderItem


def _menu(db, name="Pizza", price=14.0, cost=4.0, category="Entree"):
    item = MenuItem(name=name, category=category, price=price, cost=cost)
    db.add(item)
    db.commit()
    return item


def _order(db, item, qty=2, days_ago=0, channel="dine_in", status="completed"):
    order = Order(
        status=status,
        channel=channel,
        total=item.price * qty,
        created_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )
    order.items.append(OrderItem(menu_item_id=item.id, qty=qty, unit_price=item.price))
    db.add(order)
    db.commit()
    return order


def test_gross_profit_is_revenue_less_cost_of_goods(client, db):
    item = _menu(db, price=14.0, cost=4.0)
    _order(db, item, qty=2)

    body = client.get("/api/v1/restaurant/profit-summary").json()

    assert body["revenue"] == 28.0
    assert body["cogs"] == 8.0
    assert body["gross_profit"] == 20.0
    assert body["margin_pct"] == 71.4


def test_daily_series_sums_to_the_headline(client, db):
    """Guards a timezone bug: bucketing by local date while created_at is
    stored in UTC dropped the current day from the series but not the total."""
    item = _menu(db)
    for days_ago in (0, 1, 2):
        _order(db, item, qty=1, days_ago=days_ago)

    body = client.get("/api/v1/restaurant/profit-summary", params={"days": 7}).json()

    assert round(sum(d["revenue"] for d in body["daily"]), 2) == body["revenue"]
    assert round(sum(d["profit"] for d in body["daily"]), 2) == body["gross_profit"]
    assert sum(d["orders"] for d in body["daily"]) == body["orders"]


def test_the_series_is_continuous_including_days_with_no_trade(client, db):
    item = _menu(db)
    _order(db, item, days_ago=0)

    body = client.get("/api/v1/restaurant/profit-summary", params={"days": 5}).json()

    assert len(body["daily"]) == 5  # a gap would break the line, not skip a point
    assert sum(1 for d in body["daily"] if d["revenue"] == 0) == 4


def test_orders_outside_the_window_are_excluded(client, db):
    item = _menu(db)
    _order(db, item, days_ago=0)
    _order(db, item, days_ago=40)

    body = client.get("/api/v1/restaurant/profit-summary", params={"days": 7}).json()

    assert body["orders"] == 1


def test_cancelled_orders_do_not_count(client, db):
    item = _menu(db)
    _order(db, item, status="cancelled")

    body = client.get("/api/v1/restaurant/profit-summary").json()

    assert body["revenue"] == 0
    assert body["orders"] == 0


def test_breakdown_by_category(client, db):
    entree = _menu(db, name="Pizza", price=14.0, cost=4.0, category="Entree")
    dessert = _menu(db, name="Tiramisu", price=8.0, cost=2.0, category="Dessert")
    _order(db, entree, qty=3)
    _order(db, dessert, qty=1)

    body = client.get("/api/v1/restaurant/profit-summary").json()

    assert [c["category"] for c in body["by_category"]] == ["Entree", "Dessert"]  # by revenue
    assert body["by_category"][0]["profit"] == 30.0
    assert body["by_category"][0]["units"] == 3


def test_breakdown_by_channel(client, db):
    item = _menu(db)
    _order(db, item, qty=1, channel="dine_in")
    _order(db, item, qty=2, channel="delivery")

    body = client.get("/api/v1/restaurant/profit-summary").json()

    channels = {c["channel"]: c for c in body["by_channel"]}
    assert channels["delivery"]["revenue"] == 28.0
    assert channels["dine_in"]["orders"] == 1


def test_average_order_value(client, db):
    item = _menu(db, price=10.0, cost=3.0)
    _order(db, item, qty=1)
    _order(db, item, qty=3)

    body = client.get("/api/v1/restaurant/profit-summary").json()

    assert body["average_order_value"] == 20.0


def test_empty_restaurant_does_not_divide_by_zero(client, db):
    body = client.get("/api/v1/restaurant/profit-summary").json()

    assert body["revenue"] == 0
    assert body["margin_pct"] == 0
    assert body["average_order_value"] == 0


def test_window_length_is_bounded(client, db):
    assert client.get("/api/v1/restaurant/profit-summary", params={"days": 0}).status_code == 422
    assert client.get("/api/v1/restaurant/profit-summary", params={"days": 400}).status_code == 422
