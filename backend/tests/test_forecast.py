"""The forecast: projections that say what they are built on."""

from datetime import datetime, timedelta, timezone

from app.restaurant.models import MenuItem, Order, OrderItem


def _dish(db, price=10.0, cost=4.0):
    item = MenuItem(name="Pizza", category="Entree", price=price, cost=cost)
    db.add(item)
    db.commit()
    return item


def _sell(db, item, days_ago, qty=1):
    order = Order(
        status="completed",
        total=item.price * qty,
        created_at=datetime.now(timezone.utc).replace(hour=12) - timedelta(days=days_ago),
    )
    order.items.append(OrderItem(menu_item_id=item.id, qty=qty, unit_price=item.price))
    db.add(order)
    db.commit()


def test_no_history_projects_nothing_and_says_so(client, db):
    body = client.get("/api/v1/restaurant/forecast").json()

    assert body["method"] == "none"
    assert body["days"] == []
    assert "nothing to project" in body["message"]


def test_short_history_uses_the_daily_average(client, db):
    item = _dish(db, price=10.0, cost=4.0)
    for days_ago, qty in ((3, 1), (2, 3), (1, 2)):  # 10, 30, 20 revenue
        _sell(db, item, days_ago, qty)

    body = client.get("/api/v1/restaurant/forecast", params={"days": 7}).json()

    assert body["method"] == "average"
    assert body["confidence"] == "low"
    assert body["basis_days"] == 3
    assert len(body["days"]) == 7
    assert body["days"][0]["revenue"] == 20.0  # mean of 10, 30, 20
    assert body["days"][0]["profit"] == 12.0   # 60% margin
    assert body["total_revenue"] == 140.0


def test_quiet_days_before_the_first_order_do_not_dilute_the_average(client, db):
    item = _dish(db)
    _sell(db, item, days_ago=1, qty=5)  # one trading day, $50

    body = client.get("/api/v1/restaurant/forecast", params={"history": 28}).json()

    assert body["basis_days"] == 1
    assert body["days"][0]["revenue"] == 50.0  # not 50/28


def test_todays_partial_trading_is_excluded(client, db):
    item = _dish(db)
    _sell(db, item, days_ago=1, qty=4)
    _sell(db, item, days_ago=0, qty=1)  # today, still trading

    body = client.get("/api/v1/restaurant/forecast").json()

    assert body["days"][0]["revenue"] == 40.0


def test_the_range_brackets_the_projection(client, db):
    item = _dish(db)
    for days_ago, qty in ((3, 1), (2, 5), (1, 3)):
        _sell(db, item, days_ago, qty)

    day = client.get("/api/v1/restaurant/forecast").json()["days"][0]

    assert 0 <= day["low"] <= day["revenue"] <= day["high"]
    assert day["high"] > day["low"]


def test_two_weeks_of_trading_switches_to_weekday_means(client, db):
    item = _dish(db)
    # Every day for 21 days, busier on the same weekday as tomorrow.
    tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
    for days_ago in range(1, 22):
        day = datetime.now(timezone.utc).date() - timedelta(days=days_ago)
        _sell(db, item, days_ago, qty=9 if day.weekday() == tomorrow.weekday() else 1)

    body = client.get("/api/v1/restaurant/forecast").json()

    assert body["method"] == "weekday"
    assert body["confidence"] == "medium"
    by_date = {d["date"]: d for d in body["days"]}
    assert by_date[tomorrow.isoformat()]["revenue"] == 90.0
    quiet_days = [d for d in body["days"] if d["date"] != tomorrow.isoformat()]
    assert quiet_days and all(d["revenue"] == 10.0 for d in quiet_days)


def test_forecast_window_bounds(client, db):
    assert client.get("/api/v1/restaurant/forecast", params={"days": 0}).status_code == 422
    assert client.get("/api/v1/restaurant/forecast", params={"history": 3}).status_code == 422
