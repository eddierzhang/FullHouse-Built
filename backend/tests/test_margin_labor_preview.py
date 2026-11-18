"""The preview's one demo restaurant: every module is live and has something to show."""

from datetime import datetime, time

from app.margin_labor import channels, demand, fixtures, labor, menu_costs, usage
from app.margin_labor.models import SalesOrder, SalesOrderLine
from app.platform import flags
from app.sales.models import SalesItem
from sqlalchemy import func
from test_restaurant_setup import _set_up

API = "/api/v1/margin-labor"


def test_one_restaurant_has_all_five_modules_live_with_something_to_act_on(client, db):
    rid = _set_up(client, db)["id"]
    for flag in flags.ML_FLAGS:
        flags.set_flag(db, rid, flag, True)
    db.commit()
    service = labor.Service(db, rid)
    evening = datetime.combine(service.today, time(19, 15), tzinfo=service.tz)
    made = fixtures.seed_preview(db, rid, service.today, service.conf.timezone, evening)
    db.commit()
    menu_costs.recost(db, rid)
    db.commit()
    fixtures.seed_books(db, rid, made, service.today, service.conf.timezone)
    db.commit()
    usage.run(db, rid)
    channels.run(db, rid)
    demand.forecast_items(db, rid)
    labor.forecast_labor(db, rid)
    db.commit()

    # The order lines add up to exactly what the daily sales say was sold: one restaurant, one set of books.
    day = service.today.fromordinal(service.today.toordinal() - 3)
    by_lines = dict(db.query(SalesOrderLine.pos_item_id, func.sum(SalesOrderLine.qty)).filter_by(restaurant_id=rid, business_date=day).group_by(SalesOrderLine.pos_item_id))
    by_day = dict(db.query(SalesItem.pos_item_id, func.sum(SalesItem.qty)).filter_by(restaurant_id=rid, date=day).group_by(SalesItem.pos_item_id))
    assert {k: float(v) for k, v in by_lines.items()} == {k: float(v) for k, v in by_day.items()} == {"BURGER": 90.0, "FRIES": 72.0, "SALAD": 42.0, "WINGS": 30.0}

    costs = client.get(f"{API}/menu-costs").json()
    assert costs["state"] == "live" and costs["header"]["dishes_over"] == 1 and costs["dishes"][0]["name"] == "Burger" and costs["dishes"][0]["food_cost_pct"] == "0.328383"
    assert costs["header"]["weekly_dollars"] == "945.0000"  # $1.50 more a plate on 2,520 burgers in 28 days
    used = client.get(f"{API}/usage").json()
    assert used["state"] == "live" and used["header"] == {"weekly_dollars": "147.0000", "items": 1} and used["items"][0]["name"] == "Chicken wings"
    sold = client.get(f"{API}/channels").json()
    assert sold["state"] == "live" and sold["header"]["items"] == 1 and sold["header"]["platform"] == "Uber Eats" and sold["header"]["losing"] is True
    assert [t["label"] for t in sold["tiles"]] == ["Dine in", "DoorDash", "Uber Eats"] and 0.6 < float(sold["tiles"][0]["kept_pct"]) < 0.85
    orders = client.get(f"{API}/ordering/drafts").json()
    assert orders["state"] == "live" and orders["chart"]["points"][-1]["expected"] == 234.0 and orders["can_draft"][0]["supplier"] == "Margin Farm"
    week = client.get(f"{API}/labor/weeks/next").json()
    assert week["state"] == "live" and week["primary"] == "covers" and week["totals"]["expected"] == 136.0 * 5 + 168.0 * 2 and {r["role_key"] for r in week["roles"]} == {"server", "line_cook"}
    assert 1900 < week["days"][0]["net_expected"] < 2400  # about $2,150 of sales a day: labor is a sensible share of it
    from app.config import settings

    settings.allow_time_travel = True
    try:
        today = client.get(f"{API}/labor/today?at={service.today.isoformat()}T19:15:00").json()
    finally:
        settings.allow_time_travel = False
    assert today["state"] == "live" and today["pace"]["adjusted"] is True and 0.75 < today["pace"]["pace"] < 0.95 and today["people"][0]["on"] > 0
    cards = {c["module"] for c in client.get(f"{API}/digest-cards").json()}
    assert {"menu_costs", "usage", "channels"} <= cards


def test_the_sample_restaurant_says_the_same_thing_on_every_page(client, db):
    """The older pages (Inventory, Menu, Profit, staff) and the newer ones are written from one set of facts."""
    from app.restaurant.models import Order
    from app.stock import ledger

    rid = _set_up(client, db)["id"]
    for flag in flags.ML_FLAGS:
        flags.set_flag(db, rid, flag, True)
    db.commit()
    service = labor.Service(db, rid)
    made = fixtures.seed_preview(db, rid, service.today, service.conf.timezone, datetime.combine(service.today, time(19, 15), tzinfo=service.tz))
    db.commit()
    menu_costs.recost(db, rid)
    db.commit()
    fixtures.seed_books(db, rid, made, service.today, service.conf.timezone)
    db.commit()

    # Dishes: the Menu page and Menu costs name the same four, at the same price and the same cost per plate.
    costed = {d["name"]: d for d in client.get(f"{API}/menu-costs").json()["dishes"]}
    menu = {m["name"]: m for m in client.get("/api/v1/restaurant/menu-items").json()}
    assert set(menu) == set(costed) == {"Burger", "Fries", "House Salad", "Wings"}
    for name, dish in menu.items():
        assert f"{dish['price']:.2f}" == f"{float(costed[name]['price']):.2f}"
        assert abs(dish["cost"] - float(costed[name]["plate_cost"])) < 0.006, name
    assert menu["Burger"]["cost"] == 3.94

    # Stock: the Inventory items are the invoice items, from the one supplier, and the ledger adds up.
    stock = {i["name"]: i for i in client.get("/api/v1/restaurant/inventory-items").json()}
    assert set(stock) == set(fixtures.ITEMS) == {i["name"] for i in client.get("/api/v1/prices/items").json()}
    assert {s["name"] for s in client.get("/api/v1/restaurant/suppliers").json()} == {"Margin Farm"}
    assert stock["Ground beef"]["unit_cost"] == 22.0 and stock["Bun"]["unit_cost"] == 0.45 and all(i["quantity_on_hand"] > 0 for i in stock.values())
    assert ledger.out_of_balance(db, rid) == []

    # Sales: Profit & forecast counts the same orders, to the dollar, as the point-of-sale orders the new pages read.
    day = service.today.fromordinal(service.today.toordinal() - 3)
    pos = db.query(func.sum(SalesOrder.net)).filter_by(restaurant_id=rid, business_date=day).scalar()
    from datetime import timedelta, timezone
    start = datetime.combine(day, time(4, 0), tzinfo=service.tz).astimezone(timezone.utc)
    old = db.query(func.sum(Order.total)).filter(Order.restaurant_id == rid, Order.created_at >= start, Order.created_at < start + timedelta(days=1)).scalar()
    assert float(pos) == float(old) == 2166.0

    # People: the staff list is the team the schedule is built from, and this week's shifts are the published ones.
    staff = {p["name"] for p in client.get("/api/v1/restaurant/staff").json()}
    assert staff == set(fixtures.TEAM)
    shifts = client.get("/api/v1/restaurant/shifts").json()
    # Prime cost: a week's sales are its orders, its labor is the punched hours at each person's rate.
    from app.insights import engine

    weeks = engine.prime_cost(db, rid)
    assert len(weeks) == 8 and all(w["complete"] for w in weeks)
    assert weeks[-1]["sales"] == 2166.0 * 7 == 15162.0 and weeks[-1]["labor"] == 630.0 * 5 + 705.0 * 2 and 28 < weeks[-1]["labor_pct"] < 32
    # Stock: only the wings are under their reorder point.
    assert [i["name"] for i in stock.values() if i["low_stock"]] == ["Chicken wings"]
    ids = {p["id"] for p in client.get("/api/v1/restaurant/staff").json()}
    assert len(shifts) == 9 * 7 and {s["staff_id"] for s in shifts} == ids
