"""The preview's one demo restaurant: every module is live and has something to show."""

from datetime import datetime, time

from app.margin_labor import channels, demand, fixtures, labor, menu_costs, usage
from app.margin_labor.models import SalesOrderLine
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
    fixtures.seed_preview(db, rid, service.today, service.conf.timezone, evening)
    db.commit()
    menu_costs.recost(db, rid)
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
