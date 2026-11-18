"""Forecast core and Module C against the database (spec sections 7, 8.2, 11 and 12): item forecasts, the stock
estimate with no counts, order drafts and their corrections, prep sheets, the timed triggers, and the routes."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from app.agents.models import AgentAction
from app.forecast.models import PrepSheet
from app.margin_labor import clock, demand, fixtures, menu_costs, ordering, usage
from app.margin_labor import settings as ml_settings
from app.margin_labor.business import zone
from app.margin_labor.models import DemandDriver, ForecastInterval, ForecastRun, ItemForecast, PosItem, StockEstimate, SupplierSchedule
from app.platform import flags, jobs
from app.platform.models import Event, Job
from app.restaurant.models import PurchaseOrder, PurchaseOrderLine
from app.sales.models import SalesItem
from test_restaurant_details import _second_restaurant
from test_restaurant_setup import _set_up

API = "/api/v1/margin-labor"


class Kitchen:
    def __init__(self, client, db, schedule=True, usage_on=False, minimum=None):
        self.db, self.client = db, client
        self.rid = _set_up(client, db)["id"]
        for flag in (flags.ML_MENU_COSTS, flags.ML_ORDERING) + ((flags.ML_USAGE,) if usage_on else ()):
            flags.set_flag(db, self.rid, flag, True)
        db.commit()
        self.today = menu_costs.load(db, self.rid).today
        made = fixtures.seed_kitchen(db, self.rid, self.today)
        self.items, self.recipes, self.supplier = made["items"], made["recipes"], made["supplier"]
        if schedule:
            fixtures.seed_schedule(db, self.rid, self.supplier.id, minimum)
        db.commit()
        jobs.drain(db, queues=("derive",))
        db.commit()
        self.tz = zone(ml_settings.for_restaurant(db, self.rid).timezone)

    def draft(self, **kwargs):
        action, why = ordering.draft(self.db, self.rid, self.supplier.id, lead_hours=None, **kwargs)
        self.db.commit()
        return action, why

    def line(self, action, name):
        self.db.refresh(action)
        return next(l for l in action.payload["lines"] if l["description"] == name)

    def ref(self, action, name):
        return f"{action.id}.{self.items[name].id}"

    def estimate(self, name):
        self.db.expire_all()
        return self.db.query(StockEstimate).filter_by(restaurant_id=self.rid, canonical_item_id=self.items[name].id).one()


def test_item_forecasts_cover_fourteen_days_and_running_it_twice_writes_nothing_new(client, db):
    k = Kitchen(client, db)
    for pos, name, qty in (("TEA", "Iced Tea", 2), ("JUICE", "Juice", 1)):  # under 5 a day each: forecast as a share of Drinks
        db.add(PosItem(restaurant_id=k.rid, pos_item_id=pos, name=name, category="Drinks", price=D("3.00")))
        for back in range(1, 91):
            db.add(SalesItem(restaurant_id=k.rid, date=k.today - timedelta(days=back), pos_item_id=pos, pos_item_name=name, qty=qty, net=qty * 3.0, source="pos"))
    db.commit()
    made = demand.forecast_items(db, k.rid)
    db.commit()
    assert made["state"] == "live" and made["written"] == 6 * 15 and made["stage"] == "baseline"  # nothing to learn a driver from: the baseline stays
    rows = {(f.pos_item_id, f.business_date): f for f in db.query(ItemForecast).filter_by(restaurant_id=k.rid)}
    burger = rows[("BURGER", k.today + timedelta(days=3))]
    assert (burger.p10, burger.p50, burger.p90, burger.method, burger.stage) == (30.0, 30.0, 30.0, "item", "baseline")  # the same every day, so no range
    assert (rows[("TEA", k.today)].p50, rows[("TEA", k.today)].method, rows[("JUICE", k.today)].p50) == (2.0, "category_share", 1.0)
    total = db.query(ForecastInterval).filter_by(restaurant_id=k.rid, grain="day", metric="items", business_date=k.today).one()
    assert (total.p50, total.channel, total.kind) == (81.0, "all", "weekly")
    run = demand.latest_run(db, k.rid)
    assert run.metrics["wape_28"] == 0.0 and run.metrics["coverage_56"] == 1.0 and run.metrics["weeks_of_data"] == 12 and 8 <= len(run.metrics["weekly"]) <= 12  # a week needs four before it to be forecast
    again = demand.forecast_items(db, k.rid)
    assert again["written"] == 0 and again["run_id"] == run.id and db.query(ForecastRun).filter_by(restaurant_id=k.rid).count() == 1
    assert all(f.p10 <= f.p50 <= f.p90 and f.p10 >= 0 for f in rows.values())


def test_a_closure_is_a_forecast_of_zero_and_the_drivers_endpoints_keep_one_row_per_entry(client, db):
    k = Kitchen(client, db)
    closed = (k.today + timedelta(days=2)).isoformat()
    body = {"date": closed, "kind": "closure", "name": "Private event"}
    first = client.post(f"{API}/drivers", json=body).json()
    assert client.post(f"{API}/drivers", json=body).json()["id"] == first["id"] and db.query(DemandDriver).filter_by(restaurant_id=k.rid).count() == 1
    game = client.post(f"{API}/drivers", json={"date": (k.today + timedelta(days=4)).isoformat(), "kind": "local_event", "name": "Home game", "size": "large", "expected_multiplier": 1.3})
    assert game.status_code == 200 and game.json()["payload"] == {"size": "large", "expected_multiplier": 1.3}
    assert client.post(f"{API}/drivers", json={**body, "kind": "weather"}).status_code == 422
    assert db.query(Event).filter_by(type="drivers.updated").count() == 3
    demand.forecast_items(db, k.rid)
    db.commit()
    day = db.query(ItemForecast).filter_by(restaurant_id=k.rid, pos_item_id="BURGER", business_date=k.today + timedelta(days=2)).one()
    assert (day.p10, day.p50, day.p90) == (0.0, 0.0, 0.0) and day.drivers_applied[0]["source"] == "closure"
    shown = client.get(f"{API}/forecast/daily?metric=items&start={k.today.isoformat()}").json()["days"]
    assert shown[2]["p50"] == 0.0 and shown[2]["drivers_applied"][0]["kind"] == "closure" and shown[0]["p50"] == 78.0 and shown[0]["actual"] is None
    assert [d["name"] for d in client.get(f"{API}/drivers").json()] == ["Home game", "Private event"]
    assert client.delete(f"{API}/drivers/{first['id']}").json() == {"removed": first["id"]}
    demand.forecast_items(db, k.rid)
    db.commit()
    db.expire_all()
    assert db.query(ItemForecast).filter_by(restaurant_id=k.rid, pos_item_id="BURGER", business_date=k.today + timedelta(days=2)).one().p50 == 30.0
    # Putting the closure back gives the same inputs as two runs ago: it is still worked out again, not skipped.
    client.post(f"{API}/drivers", json=body)
    demand.forecast_items(db, k.rid)
    db.commit()
    db.expire_all()
    assert db.query(ItemForecast).filter_by(restaurant_id=k.rid, pos_item_id="BURGER", business_date=k.today + timedelta(days=2)).one().p50 == 0.0


def test_a_draft_orders_what_the_period_needs_less_the_estimate_in_whole_packs_and_sends_nothing(client, db):
    k = Kitchen(client, db)
    action, why = k.draft()
    assert why == "created" and action.action_type == "purchase_order" and action.status == "pending"
    p = action.payload
    delivery = datetime.fromisoformat(p["delivery_date"]).date()
    period_end = datetime.fromisoformat(p["period_end"]).date()
    assert delivery.weekday() in (0, 3) and delivery > k.today - timedelta(days=1)
    days = (period_end - delivery).days
    assert days == (4 if delivery.weekday() == 3 else 3)  # Thursday's order lasts to Monday; Monday's to Thursday
    assert (p["module"], p["drafted_by"], p["supplier_name"], p["check_quantities"], p["under_minimum"]) == ("ordering", "margin_labor", "Margin Farm", True, False)
    assert [l["description"] for l in p["lines"]] == sorted(fixtures.ITEMS)
    for line in p["lines"]:
        b = line["basis"]
        # Sales are the same every day, so there is no range and no buffer: the need is the expected use.
        assert D(b["root_sum_variance"]) == 0 and D(b["period_demand"]) == D(b["sum_p50"]) == D(line["expected_use"]) and D(b["loss_factor"]) == 0
        raw = D(line["need"]) - D(line["est_on_hand_at_delivery"])
        packs = 0 if raw <= 0 else int((raw / D(line["pack_base_qty"])).to_integral_value(rounding="ROUND_CEILING"))
        assert line["qty"] == line["suggested_qty"] == packs == b["packs"] and len(b["days"]) == days and b["est_anchor"]["anchor_kind"] == "start"
        assert D(line["est_on_hand_at_delivery"]) == max(D(0), D(line["est_on_hand"]) - D(b["use_before_delivery"])).quantize(D("0.0001"))
    wings = k.line(action, "Chicken wings")
    assert D(wings["expected_use"]) == D(4000) * days and wings["pack_price"] == "7.00" and wings["base_unit"] == "g" and wings["pack_base_qty"] == "1000.0000"
    assert D(p["total"]) == sum(D(l["line_total"]) for l in p["lines"]) and "Nothing is sent: you send the order." in p["note"]
    # The estimate started from half a median delivery four weeks ago and was carried forward; nothing was counted.
    est = k.estimate("Chicken wings")
    assert est.basis == {"anchor_date": (k.today - timedelta(days=28)).isoformat(), "anchor_qty": "10000.0000", "anchor_kind": "start"} and est.last_correction_at is None
    assert est.est_on_hand == D(wings["est_on_hand"]) and est.as_of == k.today

    # One draft per supplier and delivery date: asking again finds it. Nothing is ordered until a person approves.
    again, why = k.draft()
    assert (again.id, why) == (action.id, "exists") and db.query(PurchaseOrder).filter_by(restaurant_id=k.rid).count() == 0
    approved = client.post(f"/api/v1/agents/actions/{action.id}/approve")
    assert approved.status_code == 200 and "Nothing has been sent." in approved.json()["applied_result"]
    order = db.query(PurchaseOrder).filter_by(restaurant_id=k.rid).one()
    assert order.status == "draft" and order.supplier_name == "Margin Farm"  # a draft on the order's own page: a person sends it
    lines = db.query(PurchaseOrderLine).filter_by(po_id=order.id).all()
    assert len(lines) == sum(1 for l in p["lines"] if l["qty"] > 0)
    for row in lines:
        source = next(l for l in p["lines"] if l["item_id"] == row.item_id)
        assert row.qty == source["qty"] and row.suggested_qty == D(source["suggested_qty"]) and row.basis["period_start"] == p["delivery_date"]
    assert k.draft()[1] == "exists"  # decided drafts are not made again


def test_corrections_reset_the_estimate_without_a_count(client, db):
    k = Kitchen(client, db)
    action, _ = k.draft()
    wings = k.line(action, "Chicken wings")
    need, pack = D(wings["need"]), D(wings["pack_base_qty"])

    out = client.post(f"{API}/ordering/lines/{k.ref(action, 'Chicken wings')}/correction", json={"kind": "out"})
    assert out.status_code == 200
    wings = k.line(action, "Chicken wings")
    assert wings["qty"] == int(need / pack) and wings["correction"] == "out" and wings["est_on_hand"] == "0.0000"  # the whole need, with nothing on hand
    est = k.estimate("Chicken wings")
    assert est.est_on_hand == D(0) and est.basis["anchor_kind"] == "out" and est.basis["anchor_date"] == k.today.isoformat() and est.last_correction_at is not None

    plenty = client.post(f"{API}/ordering/lines/{k.ref(action, 'Ground beef')}/correction", json={"kind": "plenty"})
    beef = k.line(action, "Ground beef")
    assert plenty.status_code == 200 and beef["qty"] == 0 and beef["correction"] == "plenty" and beef["line_total"] == "0.00"
    assert k.estimate("Ground beef").est_on_hand == D(beef["need"])  # the estimate becomes the need
    assert D(plenty.json()["total"]) == sum(D(l["line_total"]) for l in action.payload["lines"])

    # Ordering two packs fewer than the line said: they know there is more on hand than the estimate.
    before = k.estimate("Chicken wings").est_on_hand
    edited = client.put(f"{API}/ordering/lines/{k.ref(action, 'Chicken wings')}", json={"qty": wings["qty"] - 2})
    assert edited.status_code == 200 and k.line(action, "Chicken wings")["qty"] == wings["qty"] - 2
    assert k.estimate("Chicken wings").est_on_hand == before + 2 * pack and k.estimate("Chicken wings").basis["anchor_kind"] == "edit"
    assert k.line(action, "Chicken wings")["suggested_qty"] == wings["suggested_qty"]  # what was suggested is kept beside what is ordered

    assert client.post(f"{API}/ordering/lines/{action.id}.nope/correction", json={"kind": "out"}).status_code == 404
    assert client.post(f"{API}/ordering/lines/{k.ref(action, 'Bun')}/correction", json={"kind": "count"}).status_code == 422
    client.post(f"/api/v1/agents/actions/{action.id}/approve")
    assert client.post(f"{API}/ordering/lines/{k.ref(action, 'Bun')}/correction", json={"kind": "out"}).status_code == 409  # already decided
    order_lines = {l.description: l for l in db.query(PurchaseOrderLine).filter_by(restaurant_id=k.rid)}
    assert "Ground beef" not in order_lines and order_lines["Chicken wings"].qty == wings["qty"] - 2 and order_lines["Chicken wings"].suggested_qty == D(wings["suggested_qty"])


def test_shelf_life_the_loss_allowance_and_the_minimum_change_the_line(client, db):
    k = Kitchen(client, db, usage_on=True, minimum="5000")
    usage.run(db, k.rid)
    k.items["Lettuce"].shelf_life_days = 2
    db.commit()
    action, _ = k.draft()
    lettuce, wings = k.line(action, "Lettuce"), k.line(action, "Chicken wings")
    # Lettuce keeps two days: only two days of the period are ordered for, at the 80% service level.
    assert lettuce["basis"]["days_used"] == 2 and lettuce["basis"]["z"] == "0.8416" and D(lettuce["expected_use"]).quantize(D("0.01")) == D("4666.67")
    assert wings["basis"]["z"] == "1.6449"
    # Usage is live and wings run 25% over: the allowance is limited to 15%.
    assert wings["basis"]["loss_factor"] == "0.150000" and D(wings["need"]) == (D(wings["expected_use"]) * D("1.15")).quantize(D("0.0001"))
    assert D(k.line(action, "Ground beef")["basis"]["loss_factor"]) == 0  # beef is bought within a delivery of what is used
    assert action.payload["under_minimum"] is True and "Under the $5,000 minimum." in action.payload["note"]


def test_drafts_come_four_hours_before_the_cutoff_one_per_delivery(client, db):
    k = Kitchen(client, db)
    schedules = db.query(SupplierSchedule).filter_by(restaurant_id=k.rid).all()
    cutoff, delivery, _row = ordering.upcoming(schedules, datetime.now(timezone.utc).astimezone(k.tz))[0]
    early = ordering.draft(db, k.rid, k.supplier.id, now=(cutoff - timedelta(hours=5)).astimezone(timezone.utc))
    assert early == (None, "too_early")
    made, why = ordering.draft(db, k.rid, k.supplier.id, now=(cutoff - timedelta(hours=3)).astimezone(timezone.utc))
    db.commit()
    assert why == "created" and made.payload["delivery_date"] == delivery.isoformat()
    # After the cutoff, the draft is for the delivery after it.
    later, why = ordering.draft(db, k.rid, k.supplier.id, now=(cutoff + timedelta(minutes=1)).astimezone(timezone.utc), lead_hours=None)
    db.commit()
    assert why == "created" and later.payload["delivery_date"] > delivery.isoformat() and later.payload["check_quantities"] is True
    after_next = datetime.fromisoformat(later.payload["cutoff_at"]) + timedelta(minutes=1)
    third, why = ordering.draft(db, k.rid, k.supplier.id, now=after_next, lead_hours=None)
    db.commit()
    assert why == "created" and third.payload["check_quantities"] is False  # the first two per supplier are labelled "Check quantities"; the third is not
    assert len({a.payload["delivery_date"] for a in db.query(AgentAction).filter_by(action_type="purchase_order")}) == 3


def test_a_fourth_order_draft_in_a_week_is_held_back_and_logged(client, db):
    from app.restaurant.po_draft import _system_run

    k = Kitchen(client, db)
    run = _system_run(db, k.rid, "Three drafts already this week")
    for n in range(3):
        db.add(AgentAction(restaurant_id=k.rid, run_id=run.id, agent_definition_id=run.agent_definition_id, action_type="purchase_order", payload={"note": f"Earlier draft {n}"}))
    db.commit()
    assert k.draft() == (None, "cap")
    assert [e.payload["kind"] for e in db.query(Event).filter_by(type="proposal.held")] == ["purchase_order"]
    refused = client.post(f"{API}/ordering/drafts", json={"supplier_id": k.supplier.id})
    assert refused.status_code == 409 and "Three order drafts" in refused.json()["detail"]


def test_the_prep_sheet_lists_each_prepped_item_with_its_par_once_per_business_date(client, db):
    k = Kitchen(client, db)
    sheet = client.get(f"{API}/prep/{k.today.isoformat()}").json()
    # 30 burgers at 20 g of house sauce each; with no range, the par is the expected use. A batch is 500 g.
    assert sheet["state"] == "live" and sheet["proposal"] is None
    assert [(l["item_name"], l["par"], l["unit"], l["batch_size"], l["batches_to_par"], l["used_by"], l["station"]) for l in sheet["lines"]] == [
        ("House sauce", "600.0000", "g", "500", 2, ["Burger"], "Kitchen")]
    made = client.post(f"{API}/prep/{k.today.isoformat()}/propose").json()
    assert made["made"] is True and client.post(f"{API}/prep/{k.today.isoformat()}/propose").json() == {"proposal_id": made["proposal_id"], "status": "pending", "made": False}
    assert db.query(AgentAction).filter_by(action_type="prep_sheet").count() == 1
    approved = client.post(f"/api/v1/agents/actions/{made['proposal_id']}/approve")
    assert approved.status_code == 200
    stored = db.query(PrepSheet).filter_by(restaurant_id=k.rid, date=k.today).one()
    assert stored.status == "confirmed" and stored.lines[0]["item_name"] == "House sauce" and stored.lines[0]["qty"] == 600.0
    assert client.get(f"{API}/prep/{k.today.isoformat()}").json()["proposal"] == {"id": made["proposal_id"], "status": "approved"}


def test_the_minute_tick_queues_the_nightly_work_and_the_draft_once_each(client, db):
    k = Kitchen(client, db)
    local_now = datetime.now(timezone.utc).astimezone(k.tz)
    nightly = local_now.replace(hour=4, minute=30, second=0, microsecond=0) + timedelta(days=1)
    assert clock.run_due(db, nightly.astimezone(timezone.utc)) >= 2
    assert clock.run_due(db, nightly.astimezone(timezone.utc)) == 0  # the same minute again queues nothing
    queued = sorted(j.payload["job"] for j in db.query(Job).filter(Job.queue == "derive", Job.status == "queued"))
    assert queued[:2] == ["forecast_daily", "prep_sheet"]
    jobs.drain(db, queues=("derive",))
    db.commit()
    assert db.query(AgentAction).filter_by(action_type="prep_sheet").count() == 1 and db.query(ItemForecast).filter_by(restaurant_id=k.rid).count() > 0

    schedules = db.query(SupplierSchedule).filter_by(restaurant_id=k.rid).all()
    cutoff = ordering.upcoming(schedules, nightly + timedelta(hours=1))[0][0]
    assert clock.run_due(db, (cutoff - timedelta(hours=6)).astimezone(timezone.utc)) == 0
    near = (cutoff - timedelta(hours=2)).astimezone(timezone.utc)
    assert clock.run_due(db, near) == 1 and clock.run_due(db, near + timedelta(minutes=1)) == 0
    assert [j.payload["job"] for j in db.query(Job).filter(Job.queue == "derive", Job.status == "queued")] == ["order_draft"]
    # With the module off, the tick queues nothing for the restaurant.
    flags.set_flag(db, k.rid, flags.ML_ORDERING, False)
    db.query(Job).filter(Job.queue == "derive").delete()
    db.commit()
    assert clock.run_due(db, nightly.astimezone(timezone.utc) + timedelta(days=1)) == 0


def test_the_ordering_page_reads_drafts_the_next_cutoff_the_chart_and_the_average_miss(client, db):
    k = Kitchen(client, db)
    empty = client.get(f"{API}/ordering/drafts").json()
    assert empty["state"] == "live" and empty["drafts"] == [] and empty["header"]["orders"] == 0 and empty["weeks_of_data"] == 12
    assert empty["header"]["next_cutoff"]["supplier"] == "Margin Farm" and empty["can_draft"][0]["supplier"] == "Margin Farm"
    assert empty["tiles"]["average_miss"] == {"value": 0.0, "then": 0.0, "stage": "baseline"}
    points = empty["chart"]["points"]
    assert len(points) == 28 and empty["chart"]["today_index"] == 14 and points[13]["actual"] == 78.0 and points[14]["actual"] is None and points[14]["expected"] == 78.0
    assert 8 <= len(empty["accuracy"]) <= 12 and empty["schedules"][0]["supplier"] == "Margin Farm"

    made = client.post(f"{API}/ordering/drafts", json={"supplier_id": k.supplier.id})
    assert made.status_code == 200 and made.json()["made"] is True
    assert client.post(f"{API}/ordering/drafts", json={"supplier_id": k.supplier.id}).json() == {"proposal_id": made.json()["proposal_id"], "status": "pending", "made": False}
    body = client.get(f"{API}/ordering/drafts").json()
    draft = body["drafts"][0]
    assert body["header"]["orders"] == 1 and body["can_draft"] == [] and draft["check_quantities"] is True and len(draft["lines"]) == 8
    line = next(l for l in draft["lines"] if l["name"] == "Chicken wings")
    assert line["line_ref"] == f"{draft['proposal_id']}.{k.items['Chicken wings'].id}" and line["pack_unit"] == "case" and line["suggested"] == line["qty"]

    why = client.get(f"{API}/why/order_lines/{line['line_ref']}").json()
    assert why["result"]["label"] == "case of Chicken wings suggested" and "rounded up to whole packs" in why["formula"]
    assert [i["label"] for i in why["inputs"]] == ["Expected use", "Buffer for a busy period", "Loss allowance", "Need", "Estimated on hand at delivery", "Pack"]
    assert why["inputs"][1]["value"] == "0 g" and why["inputs"][5]["value"] == "1,000 g" and "not a count" in why["data_basis"]
    stock = why["series"]["points"]
    assert stock[0]["marks"] == ["Delivery"] and stock[-1]["marks"] == ["Next delivery"] and D(stock[0]["value"]) >= D(stock[-1]["value"]) >= 0
    card = [c for c in client.get(f"{API}/digest-cards").json() if c["module"] == "ordering"][0]
    assert (card["orders"], card["supplier"], card["proposal_id"]) == (1, "Margin Farm", draft["proposal_id"])


def test_setup_until_there_is_a_delivery_schedule_and_another_restaurant_sees_nothing(client, db):
    k = Kitchen(client, db, schedule=False)
    body = client.get(f"{API}/ordering/drafts").json()
    assert body["state"] == "setup" and [(s["key"], s["done"]) for s in body["setup"]] == [("costs", True), ("schedule", False), ("sales", True)]
    assert body["drafts"] == [] and body["header"] is None and [s["name"] for s in body["suppliers"]] == ["Margin Farm"]
    assert client.post(f"{API}/ordering/drafts", json={"supplier_id": k.supplier.id}).status_code == 409
    saved = client.put(f"{API}/ordering/schedules", json={"supplier_id": k.supplier.id, "order_cutoff_weekday": 2, "order_cutoff_time": "14:00", "delivery_weekday": 3, "minimum_order_amount": "150"})
    assert saved.status_code == 200 and saved.json()["schedules"][0]["minimum_order_amount"] == "150.0000"
    again = client.put(f"{API}/ordering/schedules", json={"supplier_id": k.supplier.id, "order_cutoff_weekday": 2, "order_cutoff_time": "15:30", "delivery_weekday": 3})
    assert len(again.json()["schedules"]) == 1 and again.json()["schedules"][0]["order_cutoff_time"] == "15:30"  # one row per delivery day
    assert client.put(f"{API}/ordering/schedules", json={"supplier_id": k.supplier.id, "order_cutoff_weekday": 2, "order_cutoff_time": "25:00", "delivery_weekday": 3}).status_code == 422
    assert client.get(f"{API}/ordering/drafts").json()["state"] == "live"
    made = client.post(f"{API}/ordering/drafts", json={"supplier_id": k.supplier.id}).json()

    other, b = _second_restaurant(db)
    assert b.get(f"{API}/ordering/drafts").status_code == 404
    flags.set_flag(db, other.id, flags.ML_ORDERING, True)
    db.commit()
    theirs = b.get(f"{API}/ordering/drafts").json()
    assert theirs["state"] == "setup" and theirs["suppliers"] == [] and theirs["schedules"] == []
    ref = f"{made['proposal_id']}.{k.items['Bun'].id}"
    assert b.post(f"{API}/ordering/lines/{ref}/correction", json={"kind": "out"}).status_code == 404 and b.get(f"{API}/why/order_lines/{ref}").status_code == 404
    assert b.post(f"{API}/ordering/drafts", json={"supplier_id": k.supplier.id}).status_code == 409
    assert b.put(f"{API}/ordering/schedules", json={"supplier_id": k.supplier.id, "order_cutoff_weekday": 2, "order_cutoff_time": "14:00", "delivery_weekday": 3}).status_code == 404
    assert b.delete(f"{API}/ordering/schedules/{saved.json()['schedules'][0]['id']}").status_code == 404
    assert db.query(ItemForecast).filter_by(restaurant_id=other.id).count() == 0 and db.query(StockEstimate).filter_by(restaurant_id=other.id).count() == 0


def test_the_backtest_report_runs_on_all_three_fixtures(capsys):
    from app.margin_labor import backtest

    results = backtest.fixtures_report()
    assert set(results) == {"steady", "seasonal", "sparse"}
    for result in results.values():
        assert result["chosen"] in ("baseline", "drivers")
        for stage in ("baseline", "drivers"):
            r = result[stage]
            assert r["days"] >= 100 and 0 < r["wape"] < 0.25 and r["pinball"] > 0 and 0.5 < r["coverage"] <= 1
        assert result["drivers"]["wape"] <= result["baseline"]["wape"]  # the game days are learned
    assert backtest.fixtures_report() == results  # the same every run
    assert backtest.main([]) == 0 and "seasonal fixture" in capsys.readouterr().out
