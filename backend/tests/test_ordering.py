"""Phase 4: pars, supplier order days, and orders that go out by themselves inside an owner's limits.

I13: no send over a cap, for an unfamiliar item, at an unusual quantity, after a price jump or on stale stock.
I14: every send has an outbox row, a decision with its rules, and a history entry. I8, as changed: a person sends,
or the policy sends for a supplier whose owner switched autopilot on; the agent never does.
"""

import random
from datetime import date, datetime, timedelta, timezone

import pytest

from app.agents.models import AgentAction
from app.audit.models import ChangeLog
from app.ordering import autopilot, pars, policy
from app.ordering.models import AutopilotDecision, AutopilotPolicy, Outbox, SupplierCalendar
from app.platform import flags
from app.prices import demo as price_demo
from app.restaurant.models import InventoryItem, MenuItem, Order, OrderItem, PurchaseOrder, RecipeItem, Supplier
from test_restaurant_details import _second_restaurant
from test_restaurant_setup import _set_up

API = "/api/v1/ordering"
TODAY = date.today()
NOW = datetime.now(timezone.utc)


def _line(**over) -> policy.DraftLine:
    return policy.DraftLine(**{"description": "Tomatoes", "qty": 6, "unit_price": 30.0, "times_bought": 8, "median_qty": 6, "previous_price": 30.0, **over})


def _draft(**over) -> policy.Draft:
    return policy.Draft(**{"supplier_name": "Green Valley Farms", "total": 180.0, "lines": (_line(),), "order_email": "orders@gvf.example.com", **over})


ON = policy.Limits(enabled=True)


def test_an_ordinary_order_inside_every_limit_passes():
    verdict = policy.evaluate(_draft(), ON)
    assert verdict.passed and len(verdict.rules) == 10


@pytest.mark.parametrize("rule, draft, limits", [
    ("autopilot_on", _draft(), policy.Limits(enabled=False)),
    ("not_paused", _draft(), policy.Limits(enabled=True, paused=True)),
    ("by_email", _draft(channel="phone"), ON),
    ("by_email", _draft(order_email=None), ON),
    ("by_email", _draft(can_send=False), ON),
    ("per_order_cap", _draft(total=300.01), ON),
    ("weekly_cap", _draft(total=200, sent_this_week=701), ON),
    ("has_lines", _draft(lines=()), ON),
    ("known_items", _draft(lines=(_line(times_bought=3),)), ON),
    ("usual_quantity", _draft(lines=(_line(qty=10),)), ON),
    ("price_steady", _draft(lines=(_line(unit_price=33.5),)), ON),
    ("stock_fresh", _draft(stock_fresh=False, stock_detail="Sales are only in through Nov 1."), ON),
])
def test_each_rule_blocks_its_violating_draft_and_only_names_itself(rule, draft, limits):
    verdict = policy.evaluate(draft, limits)
    assert not verdict.passed
    assert [r.rule for r in verdict.failed] == [rule]
    assert verdict.failed[0].detail  # said in words


def test_no_draft_that_passes_is_ever_outside_a_limit():
    """I13, as a property over random drafts and limits."""
    rng = random.Random(13)
    passed = 0
    for _ in range(3000):
        limits = policy.Limits(enabled=rng.random() < 0.9, paused=rng.random() < 0.1, per_order_cap=rng.uniform(50, 800),
                               weekly_cap=rng.uniform(50, 2500), max_price_rise_pct=rng.uniform(0, 25), max_qty_multiple=rng.uniform(1, 3),
                               min_history=rng.randint(1, 8))
        lines = tuple(_line(qty=rng.randint(1, 20), unit_price=rng.uniform(5, 60), times_bought=rng.randint(0, 8), median_qty=rng.randint(0, 12),
                            previous_price=rng.choice([None, rng.uniform(5, 60)])) for _ in range(rng.randint(0, 5)))
        draft = _draft(total=round(sum(l.qty * l.unit_price for l in lines), 2), lines=lines, sent_this_week=rng.uniform(0, 1500),
                       channel=rng.choice(["email", "email", "phone"]), stock_fresh=rng.random() < 0.9, can_send=rng.random() < 0.9)
        if not policy.evaluate(draft, limits).passed:
            continue
        passed += 1
        assert limits.enabled and not limits.paused and draft.channel == "email" and draft.can_send and draft.stock_fresh and lines
        assert draft.total <= limits.per_order_cap and draft.sent_this_week + draft.total <= limits.weekly_cap
        for line in lines:
            assert line.times_bought >= limits.min_history
            assert line.median_qty == 0 or line.qty <= line.median_qty * limits.max_qty_multiple + 1e-9
            assert not line.previous_price or (line.unit_price - line.previous_price) / line.previous_price * 100 <= limits.max_price_rise_pct + 1e-9
    assert passed > 0


# --- the flow, on eight weeks of real invoices ---------------------------------------------------


def _restaurant(client, db, monkeypatch, mailbox=True):
    restaurant = _set_up(client, db)
    rid = restaurant["id"]
    for flag in (flags.V3_AUTOPILOT, flags.V2_ORDERS):
        flags.set_flag(db, rid, flag, True)
    suppliers = {}
    for name in price_demo.SUPPLIERS:
        supplier = Supplier(restaurant_id=rid, name=name, lead_time_days=1)
        db.add(supplier)
        db.flush()
        suppliers[name] = supplier
    price_demo.seed(db, rid, {name: s.id for name, s in suppliers.items()}, TODAY)
    db.commit()
    sent = []
    monkeypatch.setattr(autopilot.mail, "can_send", lambda: mailbox)
    monkeypatch.setattr(autopilot.po_mail, "send", lambda db_, order, to, restaurant_: sent.append((order.id, to)) or mailbox)
    return rid, suppliers, sent


def _switch_on(db, rid, supplier, **limits):
    db.add(SupplierCalendar(restaurant_id=rid, supplier_id=supplier.id, order_days=[TODAY.weekday()], cutoff_time="23:59",
                            order_email=f"orders@{supplier.name.split()[0].lower()}.example.com"))
    row = AutopilotPolicy(restaurant_id=rid, supplier_id=supplier.id, enabled=True, **{"per_order_cap": 5000, "weekly_cap": 20000, **limits})
    db.add(row)
    db.commit()
    return row


def test_acceptance_a_passing_draft_sends_once_after_the_cancel_window(client, db, monkeypatch):
    rid, suppliers, sent = _restaurant(client, db, monkeypatch)
    farm = suppliers["Green Valley Farms"]
    _switch_on(db, rid, farm)

    action = autopilot.draft_for(db, rid, farm, TODAY)
    decision = autopilot.decide(db, action, NOW, TODAY)
    db.commit()

    assert decision.outcome == "passed" and all(r["ok"] for r in decision.rules)
    order = db.query(PurchaseOrder).one()
    assert (order.status, order.autopilot, action.status) == ("draft", True, "approved")
    assert "autopilot limits" in action.applied_result
    assert decision.send_after == NOW + timedelta(minutes=30)
    # Inside the window nothing goes.
    assert autopilot.deliver_due(db, NOW + timedelta(minutes=29)) == 0 and sent == []
    # After it, once.
    assert autopilot.deliver_due(db, NOW + timedelta(minutes=31)) == 1
    assert autopilot.deliver_due(db, NOW + timedelta(minutes=45)) == 0
    db.commit()
    assert sent == [(order.id, ["orders@green.example.com"])]
    assert (order.status, order.sent_by) == ("ordered", "autopilot")
    # I14: an outbox row, a decision with its rules, and history.
    assert db.query(Outbox).one().status == "sent"
    assert db.query(AutopilotDecision).one().outcome == "sent" and len(decision.rules) == 10
    assert db.query(ChangeLog).filter(ChangeLog.entity_type == "purchase_orders").count() >= 2
    # Looked at once: the same proposal is never decided twice.
    assert autopilot.decide(db, action, NOW, TODAY) is None


def test_cancelling_inside_the_window_sends_nothing_and_leaves_a_draft(client, db, monkeypatch):
    rid, suppliers, sent = _restaurant(client, db, monkeypatch)
    farm = suppliers["Green Valley Farms"]
    _switch_on(db, rid, farm)
    autopilot.decide(db, autopilot.draft_for(db, rid, farm, TODAY), NOW, TODAY)
    db.commit()

    waiting = client.get(f"{API}/overview").json()["waiting"]
    assert [w["supplier_name"] for w in waiting] == ["Green Valley Farms"]
    assert client.post(f"{API}/decisions/{waiting[0]['id']}/cancel").json()["outcome"] == "cancelled"
    assert client.post(f"{API}/decisions/{waiting[0]['id']}/cancel").status_code == 409

    assert autopilot.deliver_due(db, NOW + timedelta(hours=2)) == 0 and sent == []
    db.expire_all()
    assert db.query(PurchaseOrder).one().status == "draft"
    assert db.query(Outbox).one().status == "cancelled"


def test_a_price_jump_holds_the_order_for_a_person_with_the_reason(client, db, monkeypatch):
    rid, suppliers, sent = _restaurant(client, db, monkeypatch)
    sysco = suppliers["Sysco Foods"]
    _switch_on(db, rid, sysco)
    then = TODAY - timedelta(days=15)  # the day after chicken first came in at $87.60, up from $75.60

    action = autopilot.draft_for(db, rid, sysco, then)
    decision = autopilot.decide(db, action, NOW, then)
    db.commit()

    assert decision.outcome == "held"
    assert [r["rule"] for r in decision.rules if not r["ok"]] == ["price_steady"]
    assert "Chicken" in action.payload["autopilot_held"][0] and "16%" in action.payload["autopilot_held"][0]
    assert action.status == "pending" and db.query(PurchaseOrder).count() == 0 and db.query(Outbox).count() == 0
    assert autopilot.deliver_due(db, NOW + timedelta(days=1)) == 0 and sent == []


def test_nothing_goes_for_a_supplier_whose_owner_has_not_switched_it_on(client, db, monkeypatch):
    rid, suppliers, sent = _restaurant(client, db, monkeypatch)
    farm = suppliers["Green Valley Farms"]
    db.add(SupplierCalendar(restaurant_id=rid, supplier_id=farm.id, order_days=list(range(7)), cutoff_time="23:59", order_email="o@gvf.example.com"))
    db.commit()

    action = autopilot.draft_for(db, rid, farm, TODAY)
    assert autopilot.decide(db, action, NOW, TODAY) is None
    result = autopilot.run(db, rid, NOW + timedelta(hours=3))
    db.commit()

    assert (result["passed"], result["sent"]) == (0, 0) and sent == []
    assert action.status == "pending" and db.query(Outbox).count() == 0 and db.query(AutopilotDecision).count() == 0


def test_pausing_or_having_no_mailbox_stops_a_send_and_three_strikes_switch_it_off(client, db, monkeypatch):
    rid, suppliers, sent = _restaurant(client, db, monkeypatch, mailbox=False)
    farm = suppliers["Green Valley Farms"]
    row = _switch_on(db, rid, farm)

    # No mailbox on the site: the order is held, not queued to fail.
    held = autopilot.decide(db, autopilot.draft_for(db, rid, farm, TODAY), NOW, TODAY)
    assert held.outcome == "held" and [r["rule"] for r in held.rules if not r["ok"]] == ["by_email"]

    # With one, a pause between approval and send keeps it in.
    monkeypatch.setattr(autopilot.mail, "can_send", lambda: True)
    db.query(AgentAction).delete()
    db.commit()
    autopilot.decide(db, autopilot.draft_for(db, rid, farm, TODAY), NOW, TODAY)
    assert client.post(f"{API}/pause", json={"paused": True}).json() == {"paused": True}
    assert autopilot.deliver_due(db, NOW + timedelta(hours=1)) == 0 and sent == []
    db.commit()
    assert db.query(PurchaseOrder).one().status == "draft"
    assert db.query(AutopilotDecision).filter_by(outcome="held").count() == 2

    # Three sends in a row that fail switch it off.
    for _ in range(3):
        autopilot._strike(db, rid, farm.id, farm.name)
    db.commit()
    db.refresh(row)
    assert row.enabled is False and "3 sends in a row" in row.paused_reason


def test_drafts_are_made_on_the_order_day_before_the_cutoff_and_respect_the_minimum(client, db, monkeypatch):
    rid, suppliers, _ = _restaurant(client, db, monkeypatch)
    farm = suppliers["Green Valley Farms"]
    local = autopilot._local(db, rid, NOW)
    cutoff = (local + timedelta(minutes=60)).strftime("%H:%M")
    calendar = SupplierCalendar(restaurant_id=rid, supplier_id=farm.id, order_days=[local.weekday()], cutoff_time=cutoff,
                                delivery_days=[(local.weekday() + 2) % 7], minimum_dollars=5000, order_email="o@gvf.example.com")
    db.add(calendar)
    db.commit()
    if (local + timedelta(minutes=60)).date() != local.date():
        pytest.skip("within an hour of midnight in the restaurant's timezone")

    assert autopilot.drafts_due(db, rid, NOW) == []  # under the $5,000 minimum: held to the next order day
    calendar.minimum_dollars = 50
    db.commit()
    made = autopilot.drafts_due(db, rid, NOW)
    assert len(made) == 1 and made[0].payload["supplier_name"] == "Green Valley Farms"
    assert date.fromisoformat(made[0].payload["expected_on"]).weekday() == (local.weekday() + 2) % 7
    assert autopilot.drafts_due(db, rid, NOW) == []  # one a day
    calendar.order_days = [(local.weekday() + 1) % 7]
    db.query(AgentAction).delete()
    db.commit()
    assert autopilot.drafts_due(db, rid, NOW) == []  # not an order day


def test_pars_come_from_sales_and_a_pinned_par_is_never_overwritten(client, db, monkeypatch):
    rid, suppliers, _ = _restaurant(client, db, monkeypatch)
    farm = suppliers["Green Valley Farms"]
    tomatoes = InventoryItem(restaurant_id=rid, name="Tomatoes", unit="lb", quantity_on_hand=10, unit_cost=1.2, supplier_id=farm.id)
    dish = MenuItem(restaurant_id=rid, name="Caprese", category="Salad", price=10, cost=2)
    db.add_all([tomatoes, dish])
    db.flush()
    db.add(RecipeItem(menu_item_id=dish.id, inventory_item_id=tomatoes.id, quantity=0.5))
    order = Order(restaurant_id=rid, status="completed", channel="dine_in", total=600, created_at=NOW - timedelta(days=2))
    db.add(order)
    db.flush()
    db.add(OrderItem(order_id=order.id, menu_item_id=dish.id, qty=60, unit_price=10))
    db.add(SupplierCalendar(restaurant_id=rid, supplier_id=farm.id, order_days=[0, 2, 4]))
    db.commit()

    row = next(r for r in client.get(f"{API}/pars").json() if r["name"] == "Tomatoes")
    # 30 lb over 30 days is 1 lb a day; lead 1 + cover 7/3 + safety days, times that.
    assert row["daily_use"] == 1.0 and row["cover_days"] == 2.33
    assert row["suggested"] == pytest.approx(1.0 * (1 + 2.33 + row["safety_days"]), abs=0.01) and row["pinned"] is False
    assert pars.recompute(db, rid) == 1 and tomatoes.par == row["suggested"]

    pinned = client.put(f"{API}/pars/{tomatoes.id}", json={"par": 40}).json()
    assert (pinned["par"], pinned["pinned"]) == (40, True)
    pars.recompute(db, rid)
    db.refresh(tomatoes)
    assert tomatoes.par == 40
    assert client.put(f"{API}/pars/{tomatoes.id}", json={"par": None}).json()["pinned"] is False
    assert pars.packs_to_par(40, 10, 5, 25) == 1 and pars.packs_to_par(40, 38, 5, 25) == 0 and pars.packs_to_par(None, 0, 0, 25) is None


def test_the_preview_replays_eight_weeks_of_invoices_against_the_limits(client, db, monkeypatch):
    rid, suppliers, _ = _restaurant(client, db, monkeypatch)
    farm = suppliers["Green Valley Farms"]
    generous = {"per_order_cap": 5000, "weekly_cap": 20000, "min_history": 2}

    wide = client.post(f"{API}/suppliers/{farm.id}/preview", json=generous).json()
    tight = client.post(f"{API}/suppliers/{farm.id}/preview", json={**generous, "per_order_cap": 200, "weekly_cap": 200}).json()

    assert len(wide["orders"]) == 8 and wide["would_send"] + wide["would_hold"] == 8
    # The first two weeks have too little history; after that the usual order goes.
    assert wide["would_send"] >= 4 and wide["orders"][0]["would_send"] is False
    assert tight["would_send"] == 0 and "limit for one order" in tight["orders"][-1]["held_for"][0]
    assert db.query(AutopilotDecision).count() == 0  # a preview changes nothing


def test_only_an_owner_sets_limits_and_everything_is_behind_the_flag_and_the_restaurant(client, db, monkeypatch):
    rid, suppliers, _ = _restaurant(client, db, monkeypatch)
    farm = suppliers["Green Valley Farms"]

    saved = client.put(f"{API}/suppliers/{farm.id}/policy", json={"enabled": True, "per_order_cap": 400, "weekly_cap": 1200}).json()
    assert (saved["enabled"], saved["per_order_cap"]) == (True, 400)
    assert client.put(f"{API}/suppliers/{farm.id}/policy", json={"enabled": True, "per_order_cap": 400, "weekly_cap": 100}).status_code == 422
    calendar = client.put(f"{API}/suppliers/{farm.id}/calendar", json={"order_days": [1, 3], "cutoff_time": "14:00", "order_email": "o@gvf.example.com"}).json()
    assert calendar["order_days"] == [1, 3]
    assert client.put(f"{API}/suppliers/{farm.id}/calendar", json={"order_days": [9]}).status_code == 422

    from app.auth.models import User

    db.query(User).filter(User.restaurant_id == rid).update({"role": "admin"})
    db.commit()
    assert client.put(f"{API}/suppliers/{farm.id}/policy", json={"enabled": True}).status_code == 403  # a manager, not the owner

    other, b = _second_restaurant(db)
    assert b.get(f"{API}/overview").status_code == 404  # the flag is off there
    flags.set_flag(db, other.id, flags.V3_AUTOPILOT, True)
    db.commit()
    assert b.get(f"{API}/overview").json()["suppliers"] == []
    assert b.put(f"{API}/suppliers/{farm.id}/policy", json={"enabled": True}).status_code == 404
    assert b.post(f"{API}/suppliers/{farm.id}/preview", json={}).status_code == 404
