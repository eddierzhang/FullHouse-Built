"""Margin & Labor Engine, Phase 0: the shared data. Business date, CSV imports that are safe to repeat, the
interval rollup as a pure function, tenant isolation for every new table, and reads that stay fast."""

import pathlib
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func

from app.margin_labor import csv_import, models, rollup, synthetic
from app.margin_labor import settings as ml_settings
from app.margin_labor.business import business_date, day_bounds, interval_start
from app.platform import flags, jobs
from app.restaurant.models import Staff
from app.sales.models import SalesItem
from test_restaurant_details import _second_restaurant
from test_restaurant_setup import _set_up

API = "/api/v1/margin-labor"
FIXTURES = pathlib.Path(__file__).parent / "fixtures_ml"
NY = "America/New_York"


def _restaurant(client, db):
    rid = _set_up(client, db)["id"]
    flags.set_flag(db, rid, flags.ML_MENU_COSTS, True)
    db.commit()
    return rid


def _counts(db, rid) -> dict:
    return {m.__tablename__: db.query(func.count(m.id)).filter(m.restaurant_id == rid).scalar()
            for m in (models.SalesOrder, models.SalesOrderLine, models.PosItem, models.LaborPunch, models.ChannelPayout, models.IntervalActual)}


# --- golden: business date (spec section 4) ------------------------------------------------------------


def test_golden_business_date_a_sale_at_0130_saturday_is_friday_and_at_0400_is_saturday():
    saturday = date(2026, 11, 14)
    at = lambda hour, minute: datetime(2026, 11, 14, hour, minute, tzinfo=ZoneInfo(NY))  # noqa: E731
    assert business_date(at(1, 30), NY, "04:00") == date(2026, 11, 13)   # Friday night
    assert business_date(at(4, 0), NY, "04:00") == saturday
    assert business_date(at(3, 59), NY, "04:00") == date(2026, 11, 13)
    # The same moment given in UTC is the same business date: the zone is the restaurant's, not the timestamp's.
    assert business_date(at(1, 30).astimezone(timezone.utc), NY, "04:00") == date(2026, 11, 13)
    start, end = day_bounds(saturday, NY, "04:00")
    assert business_date(start, NY) == saturday and business_date(end - timedelta(seconds=1), NY) == saturday
    assert business_date(end, NY) == date(2026, 11, 15)
    assert interval_start(at(18, 22)).astimezone(ZoneInfo(NY)).strftime("%H:%M") == "18:15"


def test_business_date_holds_across_a_daylight_saving_change():
    # Nov 1 2026: clocks go back at 02:00 in New York. 01:30 happens twice; both belong to Saturday Oct 31.
    first = datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)   # 01:30 EDT
    second = datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)  # 01:30 EST
    assert business_date(first, NY) == business_date(second, NY) == date(2026, 10, 31)


# --- acceptance: a fixture CSV imported twice gives the same rows ----------------------------------------


def test_acceptance_importing_each_fixture_twice_leaves_the_same_rows(client, db):
    rid = _restaurant(client, db)
    db.add(Staff(restaurant_id=rid, name="Ana Rivera", role="Server"))
    db.commit()

    first = {kind: client.post(f"{API}/data/import", json={"kind": kind, "text": (FIXTURES / f"{kind}.csv").read_text()}).json()
             for kind in ("orders", "punches", "payouts")}
    after_first = _counts(db, rid)
    second = {kind: client.post(f"{API}/data/import", json={"kind": kind, "text": (FIXTURES / f"{kind}.csv").read_text()}).json()
              for kind in ("orders", "punches", "payouts")}

    assert _counts(db, rid) == after_first
    assert after_first == {"sales_orders": 5, "sales_order_lines": 8, "pos_items": 6, "labor_punches": 3, "channel_payouts": 2,
                           "interval_actuals": after_first["interval_actuals"]}
    assert (first["orders"]["created"], second["orders"]["created"], second["orders"]["unchanged"]) == (5, 0, 5)
    assert (first["punches"]["created"], second["punches"]["unchanged"]) == (3, 3)
    assert (first["payouts"]["created"], second["payouts"]["unchanged"]) == (2, 2)
    # Rows that could not be read are left out and said, with their line: a bad time, no order id, a punch that ends before it starts.
    assert [(p["line"], p["problem"][:22]) for p in first["orders"]["problems"]] == [(10, "Order A-1006: 'not a t"), (11, "No order id.")]
    assert first["punches"]["problems"] == [{"line": 5, "problem": "Clocks out before it clocks in."}]


def test_an_import_reads_channels_money_voids_and_the_business_date(client, db):
    rid = _restaurant(client, db)
    csv_import.import_orders(db, rid, (FIXTURES / "orders.csv").read_text())
    csv_import.import_punches(db, rid, (FIXTURES / "punches.csv").read_text())
    csv_import.import_payouts(db, rid, (FIXTURES / "payouts.csv").read_text())
    db.commit()
    orders = {o.external_id: o for o in db.query(models.SalesOrder)}

    dinner = orders["A-1001"]
    assert (dinner.channel, dinner.covers, dinner.net, dinner.tax, dinner.tips) == ("dine_in", 2, Decimal("29.0000"), Decimal("2.4000"), Decimal("5.0000"))
    assert isinstance(dinner.net, Decimal)  # money is never a float
    late = orders["A-1003"]  # 01:30 on Saturday the 14th, the restaurant's clock
    assert (late.business_date, late.channel, late.platform) == (date(2026, 11, 13), "delivery_platform", "DoorDash")
    assert orders["A-1004"].business_date == date(2026, 11, 14)  # 04:00: the new day
    assert orders["A-1002"].channel == "takeout" and orders["A-1002"].covers is None  # no guest count is not zero guests
    # One line of A-1005 was voided after the kitchen made it: out of the order's sales, kept as a line.
    lunch = orders["A-1005"]
    lines = db.query(models.SalesOrderLine).filter_by(order_id=lunch.id).order_by(models.SalesOrderLine.line_no).all()
    assert lunch.net == Decimal("36.0000") and [(l.pos_item_id, l.voided, l.voided_after_fire) for l in lines] == [("WINGS", True, True), ("BURGER", False, False)]
    first_line = db.query(models.SalesOrderLine).filter_by(order_id=dinner.id, line_no=1).one()
    assert first_line.modifiers == ["No onion", "Extra pickles"] and first_line.qty == Decimal("2.0000")
    assert db.query(models.PosItem).filter_by(pos_item_id="BURGER").one().price == Decimal("12.0000")

    punches = {p.external_id: p for p in db.query(models.LaborPunch)}
    assert (punches["P-2"].role_key, punches["P-2"].business_date, punches["P-3"].clock_out) == ("line_cook", date(2026, 11, 13), None)
    payouts = {p.external_order_id: p for p in db.query(models.ChannelPayout)}
    # What the platform took is stored as a cost; the payout row found its order by the platform's order id.
    assert (payouts["A-1003"].commission, payouts["A-1003"].fees, payouts["A-1003"].order_id) == (Decimal("4.8800"), Decimal("0.5900"), late.id)
    assert payouts["A-9999"].order_id is None and payouts["A-9999"].adjustments == Decimal("-2.0000")


def test_a_changed_export_updates_the_same_order_and_a_file_missing_a_column_is_refused(client, db):
    rid = _restaurant(client, db)
    text = (FIXTURES / "orders.csv").read_text()
    csv_import.import_orders(db, rid, text)
    db.commit()

    changed = csv_import.import_orders(db, rid, text.replace("SALAD,House Salad,Salads,9.00,1,9.00", "SALAD,House Salad,Salads,9.00,2,18.00"))
    db.commit()
    assert (changed.created, changed.updated, changed.unchanged) == (0, 1, 4)
    assert db.query(models.SalesOrder).filter_by(external_id="A-1002").one().net == Decimal("18.0000")
    assert db.query(models.SalesOrder).filter_by(restaurant_id=rid).count() == 5

    refused = client.post(f"{API}/data/import", json={"kind": "orders", "text": "Order ID,Item\n1,Burger\n"}).json()
    assert refused["orders"] == 0 and "no column for: opened_at, qty, line_net" in refused["problems"][0]["problem"]
    assert client.post(f"{API}/data/import", json={"kind": "invoices", "text": "x"}).status_code == 422


# --- the rollup is a pure function of the day's orders ---------------------------------------------------


def test_rollup_intervals_counts_each_order_once_in_its_interval_and_running_it_twice_changes_nothing(client, db):
    rid = _restaurant(client, db)
    csv_import.import_orders(db, rid, (FIXTURES / "orders.csv").read_text())
    db.commit()
    assert jobs.drain(db, queues=("derive",)) == 2  # one job per business date touched
    friday = date(2026, 11, 13)
    local = ZoneInfo(ml_settings.for_restaurant(db, rid).timezone)  # the file's times are the restaurant's own clock

    def rows():
        return [(r.interval_start.astimezone(local).strftime("%a %H:%M"), r.channel, r.orders, r.covers, r.items, r.net)
                for r in db.query(models.IntervalActual).filter_by(restaurant_id=rid, business_date=friday).order_by(models.IntervalActual.interval_start)]

    first = rows()
    assert first == [("Fri 18:00", "dine_in", 1, 2, Decimal("3.0000"), Decimal("29.0000")),
                     ("Fri 18:15", "takeout", 1, None, Decimal("1.0000"), Decimal("9.0000")),
                     ("Sat 01:30", "delivery_platform", 1, None, Decimal("3.0000"), Decimal("19.5000"))]
    rollup.rollup_intervals(db, rid, friday)
    rollup.rollup_intervals(db, rid, friday)
    db.commit()
    assert rows() == first
    # The interval totals are the orders' totals: nothing is lost or counted twice.
    total = db.query(func.sum(models.IntervalActual.net)).filter_by(restaurant_id=rid).scalar()
    assert Decimal(str(total)).quantize(Decimal("0.01")) == sum(o.net for o in db.query(models.SalesOrder).filter_by(restaurant_id=rid))


def test_the_rollup_fills_the_per_item_table_only_where_nothing_else_supplies_the_day(client, db):
    rid = _restaurant(client, db)
    saturday, friday = date(2026, 11, 14), date(2026, 11, 13)
    db.add(SalesItem(restaurant_id=rid, date=friday, pos_item_id="BURGER", pos_item_name="Burger", qty=40, net=480.0, source="square"))
    db.commit()
    csv_import.import_orders(db, rid, (FIXTURES / "orders.csv").read_text())
    db.commit()
    jobs.drain(db, queues=("derive",))

    by_day = lambda day: sorted((r.source, r.pos_item_id, r.qty) for r in db.query(SalesItem).filter_by(restaurant_id=rid, date=day))  # noqa: E731
    assert by_day(friday) == [("square", "BURGER", 40)]  # the POS connection already supplies Friday: left exactly as it was
    assert by_day(saturday) == [("orders", "BURGER", 3.0), ("orders", "PASTA", 1.0)]  # the voided wings are not a sale


# --- tenants, flags and the agent ------------------------------------------------------------------------


TABLES = [models.PosItem, models.SalesOrder, models.SalesOrderLine, models.LaborPunch, models.ChannelPayout, models.DemandDriver,
          models.ItemConversion, models.SupplierSchedule, models.RestaurantSettings, models.ChannelTerms, models.IntervalActual,
          models.ForecastRun, models.ForecastInterval, models.Finding]


@pytest.mark.parametrize("model", TABLES, ids=lambda m: m.__tablename__)
def test_every_new_table_is_a_tenant_table_whose_index_leads_with_the_restaurant(model):
    table = model.__table__
    assert "restaurant_id" in table.c and not table.c.restaurant_id.nullable
    leading = [list(i.columns)[0].name for i in table.indexes] + [list(c.columns)[0].name for c in table.constraints
                                                                  if getattr(c, "columns", None) is not None and len(list(c.columns))]
    assert "restaurant_id" in leading, f"{table.name} has no index or key that leads with restaurant_id"


def test_another_restaurant_sees_none_of_it_and_the_routes_are_behind_the_module_flags(client, db):
    rid = _restaurant(client, db)
    client.post(f"{API}/data/import", json={"kind": "orders", "text": (FIXTURES / "orders.csv").read_text()})
    mine = client.get(f"{API}/data/status").json()
    assert (mine["orders"]["rows"], mine["orders"]["business_dates"], mine["orders"]["rolled_up_dates"]) == (5, 2, 2)

    other, b = _second_restaurant(db)
    assert b.get(f"{API}/data/status").status_code == 404  # no module is on there
    flags.set_flag(db, other.id, flags.ML_LABOR, True)
    db.commit()
    theirs = b.get(f"{API}/data/status").json()
    assert (theirs["orders"]["rows"], theirs["punches"]["rows"], theirs["payouts"]["rows"]) == (0, 0, 0)
    # The same order ids imported by another restaurant are its own orders, not updates to the first one's.
    b.post(f"{API}/data/import", json={"kind": "orders", "text": (FIXTURES / "orders.csv").read_text()})
    assert db.query(models.SalesOrder).filter_by(restaurant_id=rid).count() == 5
    assert db.query(models.SalesOrder).filter_by(restaurant_id=other.id).count() == 5
    assert {o.business_date for o in db.query(models.SalesOrder).filter_by(restaurant_id=other.id, external_id="A-1003")} == {date(2026, 11, 13)}  # Denver: 01:30 is still Friday


def test_the_agent_cannot_write_any_new_table_from_a_tool(db):
    from app.agents.guard import ForbiddenAgentWrite, tool_scope

    for model in TABLES:
        with tool_scope("a_tool"):
            db.add(model())
            with pytest.raises(ForbiddenAgentWrite):
                db.flush()
        db.rollback()


def test_settings_have_the_specs_defaults_until_an_owner_changes_them(client, db):
    rid = _restaurant(client, db)
    conf = ml_settings.for_restaurant(db, rid)
    assert (conf.business_day_start, conf.target_food_cost_pct, conf.price_ending, conf.costing_price_policy) == ("04:00", Decimal("0.30"), Decimal("0.00"), "latest")
    assert ml_settings.value(db, rid, "usage_min_weekly_dollars") == Decimal("25")
    db.add(models.RestaurantSettings(restaurant_id=rid, target_food_cost_pct=Decimal("0.28"), overrides={"usage_min_weekly_dollars": "40"}))
    db.commit()
    assert ml_settings.for_restaurant(db, rid).target_food_cost_pct == Decimal("0.2800")
    assert ml_settings.value(db, rid, "usage_min_weekly_dollars") == Decimal("40")


# --- synthetic data and read performance -----------------------------------------------------------------


def test_synthetic_history_is_repeatable_and_its_three_shapes_differ(client, db):
    rid = _restaurant(client, db)
    today = date(2026, 11, 17)
    made = synthetic.generate(db, rid, days=28, profile="steady", seed=7, today=today)
    db.commit()
    assert made["orders"] > 2000 and made["punches"] > 100
    per_day = dict(db.query(models.SalesOrder.business_date, func.count(models.SalesOrder.id)).filter_by(restaurant_id=rid).group_by(models.SalesOrder.business_date))
    weekend = [n for d, n in per_day.items() if d.weekday() >= 4]
    weekday = [n for d, n in per_day.items() if d.weekday() < 4]
    assert sum(weekend) / len(weekend) > sum(weekday) / len(weekday)

    import random

    steady = [synthetic._orders_for(today - timedelta(days=i), "steady", random.Random(1)) for i in range(365)]
    seasonal = [synthetic._orders_for(today - timedelta(days=i), "seasonal", random.Random(1)) for i in range(365)]
    sparse = [synthetic._orders_for(today - timedelta(days=i), "sparse", random.Random(1)) for i in range(365)]
    assert max(seasonal) / max(1, min(seasonal)) > 2 * max(steady) / max(1, min(steady))
    assert sum(sparse) < sum(steady) / 4 and sparse.count(0) >= 100  # closed two days a week


def test_reads_of_the_shared_data_stay_fast_with_other_restaurants_history_beside_it(client, db):
    """The spec's target is a module page under 300 ms at the 95th percentile. No module page exists yet, so this
    times the three indexed reads those pages will make, on one restaurant among several with months of orders."""
    rid = _restaurant(client, db)
    today = date(2026, 11, 17)
    synthetic.generate(db, rid, days=120, profile="steady", seed=1, today=today, punches=False)
    for n in range(4):
        other, _ = _second_restaurant_n(db, n)
        synthetic.generate(db, other, days=120, profile="seasonal", seed=n + 2, today=today, punches=False)
    db.commit()
    for back in range(1, 29):
        rollup.rollup_intervals(db, rid, today - timedelta(days=back))
    db.commit()

    def page() -> None:
        since = today - timedelta(days=28)
        db.query(models.IntervalActual).filter(models.IntervalActual.restaurant_id == rid, models.IntervalActual.business_date >= since).all()
        db.query(models.SalesOrderLine.pos_item_id, func.sum(models.SalesOrderLine.qty), func.sum(models.SalesOrderLine.net)).filter(
            models.SalesOrderLine.restaurant_id == rid, models.SalesOrderLine.business_date >= since, models.SalesOrderLine.voided.is_(False)
        ).group_by(models.SalesOrderLine.pos_item_id).all()
        db.query(models.SalesOrder.channel, func.count(models.SalesOrder.id), func.sum(models.SalesOrder.net)).filter(
            models.SalesOrder.restaurant_id == rid, models.SalesOrder.business_date >= since).group_by(models.SalesOrder.channel).all()
        db.expire_all()

    times = []
    for _ in range(20):
        started = time.perf_counter()
        page()
        times.append(time.perf_counter() - started)
    p95 = sorted(times)[int(len(times) * 0.95) - 1]
    assert p95 < 0.300, f"three indexed reads took {p95 * 1000:.0f} ms at the 95th percentile"


def _second_restaurant_n(db, n: int):
    from app.restaurant.models import Restaurant

    other = Restaurant(name=f"Other {n}", city="Denver", country="United States", timezone="America/Denver", setup_completed_at=datetime.now(timezone.utc))
    db.add(other)
    db.flush()
    return other.id, None
