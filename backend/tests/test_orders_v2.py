"""Phase C: purchase-order drafts, forecasting, prep sheets and workflows.

Eight weeks of invoices from Chef's Warehouse; the po_draft workflow proposes an order whose kale quantity is the
8-week median; a manager approves and sends it and the mail goes through the stub; an invoice with less kale than
the order opens a short-delivery card; six weeks of sales make a forecast within one MAD of the same-weekday mean;
actuals write an error row and the accuracy line; the prep sheet carries forecast × recipe; the agent cannot set an
order to sent and a bookkeeper cannot send one.
"""

import statistics
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.agents.models import AgentAction, AgentRun
from app.auth.models import BOOKKEEPER
from app.forecast import engine as forecast
from app.forecast import prep
from app.forecast.models import Forecast, ForecastError
from app.platform import flags, jobs, workflows
from app.platform.models import Job, Workflow
from app.prices import engine, mail
from app.prices.models import PriceItem
from app.recipes import engine as recipes
from app.restaurant import po_draft
from app.restaurant.models import PurchaseOrder, Supplier
from app.sales.models import SalesItem
from test_restaurant_setup import _set_up

TODAY = date.today()
ORDERS = "/api/v1/restaurant/purchase-orders"


def _on(client, db) -> dict:
    restaurant = _set_up(client, db)
    for flag in (flags.V2_ORDERS, flags.V2_DOCUMENTS, flags.V2_CONNECT):
        flags.set_flag(db, restaurant["id"], flag, True)
    db.commit()
    return restaurant


def _invoice(client, supplier: str, day: date, lines: list[tuple]) -> dict:
    saved = client.post("/api/v1/prices/invoices", json={
        "supplier_name": supplier, "invoice_date": day.isoformat(), "source": "typed",
        "lines": [{"description": d, "qty": q, "pack_text": p, "unit_price": u} for d, q, p, u in lines],
    })
    assert saved.status_code == 201, saved.text
    return saved.json()


def _answer_all(client):
    for q in client.get("/api/v1/prices/questions").json():
        if q["status"] == "open":
            client.post(f"/api/v1/prices/questions/{q['id']}/answer", json={"answer": "yes"})


def _pending(client, kind=None):
    rows = client.get("/api/v1/agents/actions?status=pending").json()
    return [a for a in rows if kind is None or a["action_type"] == kind]


#: Kale cases on the eight weekly invoices, newest last. Median 10.
KALE_WEEKS = [8, 12, 10, 9, 14, 10, 11, 10]


def _eight_weeks(client, db):
    for back, cases in zip(range(8, 0, -1), KALE_WEEKS):
        day = TODAY - timedelta(days=7 * back - 2)
        _invoice(client, "Chef's Warehouse", day, [("KALE GRN 24CT", cases, "CS", 26.00), ("OLIVE OIL EV 4/1 GAL", 1, "CS", 68.00)])
        _answer_all(client)


def test_acceptance_order_draft_send_short_delivery_forecast_prep_and_roles(client, db, monkeypatch):
    restaurant = _on(client, db)
    rid = restaurant["id"]
    _eight_weeks(client, db)
    kale = db.query(PriceItem).filter(PriceItem.name.ilike("%kale%")).one()
    chefs = db.query(Supplier).filter_by(name="Chef's Warehouse").one()
    assert db.query(engine.Invoice).filter_by(supplier_id=chefs.id).count() == 8

    # --- the po_draft workflow drafts this week's order: kale at the 8-week median, at the latest price.
    workflows.ensure_defaults(db, rid)
    db.commit()
    assert client.post("/api/v1/platform/workflows/po_draft/run").json()["queued"] is True
    assert jobs.drain(db) >= 1
    (proposal,) = _pending(client, "purchase_order")
    kale_line = next(l for l in proposal["payload"]["lines"] if l["item_id"] == kale.id)
    assert kale_line["qty"] == statistics.median(KALE_WEEKS) == 10 and kale_line["unit_price_expected"] == 26.0
    assert proposal["payload"]["supplier_name"] == "Chef's Warehouse" and "Drafted, not sent" in proposal["payload"]["note"]
    assert set(kale_line["cited_line_ids"]) <= {l.id for l in db.query(engine.InvoiceLine)}
    run = db.get(AgentRun, proposal["run_id"])
    assert run.cost_usd is not None  # 0.0 here: the draft is by rule, no model was billed
    # Running the workflow again this week makes no second proposal.
    client.post("/api/v1/platform/workflows/po_draft/run")
    jobs.drain(db)
    assert len(_pending(client, "purchase_order")) == 1

    # --- a manager approves and sends it: the mail goes through the stub, with a PDF, and the order is sent.
    sent = []
    monkeypatch.setattr(mail, "send", lambda to, subject, html, text, attachments=None: sent.append((to, subject, text, attachments)) or True)
    approved = client.post(f"/api/v1/agents/actions/{proposal['id']}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["applied_result"].startswith("Drafted an order of 2 lines to Chef's Warehouse")
    (order,) = client.get(ORDERS).json()
    assert order["status"] == "draft" and "kale" in [l["description"] for l in order["lines"]][0].lower()
    no_rep = client.post(f"{ORDERS}/{order['id']}/send", json={})
    assert no_rep.status_code == 422  # nobody to send to yet
    delivered = client.post(f"{ORDERS}/{order['id']}/send", json={"to": "orders@chefswarehouse.example"})
    assert delivered.status_code == 200, delivered.text
    assert delivered.json()["status"] == "ordered" and delivered.json()["sent_by"] == "eddie@example.com"
    (to, subject, text, attachments) = sent[0]
    assert to == ["orders@chefswarehouse.example"] and "Purchase order" in subject and "10 case" in text
    assert attachments and attachments[0][0].startswith("purchase-order-") and attachments[0][2].startswith(b"%PDF")
    assert client.post(f"{ORDERS}/{order['id']}/send", json={"to": "x@y.z"}).status_code == 409  # not twice

    # --- the invoice brings less kale than ordered: the order's line is received short, and the digest says so.
    short = _invoice(client, "Chef's Warehouse", TODAY, [("KALE GRN 24CT", 8, "CS", 26.00), ("OLIVE OIL EV 4/1 GAL", 1, "CS", 68.00)])
    assert short["matched"] == 2
    db.expire_all()
    po = db.get(PurchaseOrder, order["id"])
    kale_po_line = next(l for l in po.lines if l.item_id == kale.id)
    assert kale_po_line.received_qty == 8 and kale_po_line.invoice_line_id
    assert po.status == "received"
    digest = client.post("/api/v1/prices/digest/rebuild").json()
    owed = next(s for s in digest["sections"] if s["key"] == "owed")
    short_card = next(c for c in owed["cards"] if "Short 2" in c["sentence"])
    assert short_card["impact"] == 52.0 and short_card["action"]["draft_kind"] == "short_delivery"
    assert any(src["id"] == short["id"] for src in short_card["sources"])

    # --- six weeks of sales make a forecast for next Tuesday within one MAD of the same-weekday mean.
    rows = ["Date,Time,Category,Item,Qty,Price Point Name,SKU,Gross Sales,Discounts,Net Sales,Tax"]
    tuesdays = []
    for back in range(1, 43):
        d = TODAY - timedelta(days=back)
        base = 30 if d.weekday() == 1 else 20
        qty = base + (back % 3)
        if d.weekday() == 1:
            tuesdays.append(qty)
        rows.append(f"{d.strftime('%m/%d/%Y')},12:00:00,Food,Kale Caesar,{qty},Regular,KC,{qty * 14:.2f},0.00,{qty * 14:.2f},0.00")
    assert client.post("/api/v1/connect/csv/import", json={"text": "\n".join(rows) + "\n"}).status_code == 200
    jobs.drain(db)  # the import queues a forecast through sales_items.synced
    next_tuesday = TODAY + timedelta(days=(1 - TODAY.weekday()) % 7 or 7)
    row = db.query(Forecast).filter_by(restaurant_id=rid, pos_item_id="KC", date=next_tuesday).one()
    recent = tuesdays[:6]
    mean = sum(recent) / len(recent)
    median = statistics.median(recent)
    mad = statistics.median(abs(v - median) for v in recent)
    assert abs(row.qty_pred - mean) <= max(mad, 1.0) + 0.5, (row.qty_pred, mean, mad)
    assert row.qty_lo <= row.qty_pred <= row.qty_hi
    page = client.get("/api/v1/forecast").json()
    assert any(r["pos_item_id"] == "KC" and r["date"] == next_tuesday.isoformat() for r in page["rows"])

    # --- actuals arrive for a forecast day: an error row, and the accuracy line in the digest.
    yesterday = TODAY - timedelta(days=1)
    db.add(Forecast(restaurant_id=rid, pos_item_id="KC", pos_item_name="Kale Caesar", date=yesterday, qty_pred=22, qty_lo=20, qty_hi=24))
    last_week = engine.week_start(TODAY) - timedelta(days=3)
    db.add(Forecast(restaurant_id=rid, pos_item_id="KC", pos_item_name="Kale Caesar", date=last_week, qty_pred=25, qty_lo=22, qty_hi=28))
    db.commit()
    forecast.run(db, rid, TODAY)
    db.commit()
    error = db.query(ForecastError).filter_by(restaurant_id=rid, pos_item_id="KC", date=last_week).one()
    actual = db.query(SalesItem).filter_by(restaurant_id=rid, pos_item_id="KC", date=last_week).one().qty
    assert error.qty_actual == actual and error.abs_pct_err == round(abs(25 - actual) / actual * 100, 2)
    digest = client.get("/api/v1/prices/digest").json()
    assert digest["forecast_accuracy"] is not None and 0 <= digest["forecast_accuracy"] <= 100
    from app.restaurant.models import Restaurant

    _, _, text_body = mail.render(db, engine.latest_digest(db, rid), db.get(Restaurant, rid), "https://x.test", "https://x.test")
    assert f"Forecast accuracy last week: {digest['forecast_accuracy']:g}%" in text_body

    # --- a confirmed recipe for Kale Caesar; the prep sheet carries forecast × recipe for kale.
    recipe = recipes.write_recipe(db, rid, {"name": "Kale Caesar", "pos_item_id": "KC", "yield_qty": 1, "yield_unit": "plate",
                                            "lines": [{"item_id": kale.id, "qty": 2, "unit": "ct", "ingredient_text": "kale"}]})
    db.commit()
    assert client.post("/api/v1/platform/workflows/prep_sheet/run").json()["queued"] is True
    jobs.drain(db)
    assert [(j.queue, j.error) for j in db.query(Job).filter(Job.status == "failed")] == []
    sheets = _pending(client, "prep_sheet")
    assert sheets, "no prep sheet proposed"
    tomorrow = TODAY + timedelta(days=1)
    sheet = next(a for a in sheets if a["payload"]["date"] == tomorrow.isoformat())
    predicted = db.query(Forecast).filter_by(restaurant_id=rid, pos_item_id="KC", date=tomorrow).one().qty_pred
    kale_prep = next(l for l in sheet["payload"]["lines"] if l["item_id"] == kale.id)
    assert kale_prep["qty"] == round(2 * predicted, 3) and kale_prep["unit"] == "ct"
    assert client.post(f"/api/v1/agents/actions/{sheet['id']}/approve").status_code == 200
    (printed,) = client.get("/api/v1/prep-sheets").json()
    assert printed["date"] == tomorrow.isoformat() and printed["status"] == "confirmed"
    assert client.post(f"/api/v1/prep-sheets/{printed['id']}/printed").json()["status"] == "printed"

    # --- the agent cannot set an order to sent; a bookkeeper cannot send one.
    from app.agents.guard import ForbiddenAgentWrite, tool_scope
    from app.main import app

    draft = PurchaseOrder(restaurant_id=rid, supplier_id=chefs.id, supplier_name=chefs.name, item_name="1 item", quantity=1, unit="lines", status="draft")
    db.add(draft)
    db.commit()
    with tool_scope("propose_purchase_order"):
        draft.status = "ordered"
        with pytest.raises(ForbiddenAgentWrite):
            db.flush()
    db.rollback()
    token = client.post("/api/v1/auth/invites", json={"role": BOOKKEEPER, "email": "books@example.com"}).json()["token"]
    books = TestClient(app)
    assert books.post(f"/api/v1/auth/invite/{token}", json={"name": "Pat Books", "email": "books@example.com", "password": "a-long-password"}).status_code == 201
    assert books.post(f"{ORDERS}/{draft.id}/send", json={"to": "x@y.z"}).status_code in (403, 404)
    assert books.post(f"{ORDERS}/{draft.id}/mark-sent").status_code in (403, 404)


def test_default_workflows_are_made_at_setup_and_the_minute_tick_queues_each_once_per_period(client, db):
    restaurant = _set_up(client, db)
    kinds = {w.kind: w for w in db.query(Workflow).filter_by(restaurant_id=restaurant["id"])}
    assert set(kinds) == set(workflows.DEFAULTS)
    assert kinds["digest"].enabled is True and kinds["po_draft"].enabled is False and kinds["calendar_scan"].enabled is True
    # Monday 05:30 on the restaurant's clock (America/Los_Angeles in the setup fixture) queues the digest rebuild, once.
    from zoneinfo import ZoneInfo

    local = datetime(2026, 11, 2, 5, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
    now = local.astimezone(timezone.utc)
    assert workflows.run_due(db, now) == 1
    assert workflows.run_due(db, now) == 0  # the same minute again: the singleton key holds
    (job,) = db.query(Job).filter(Job.queue == "detect").all()
    assert job.singleton_key == f"workflow:{restaurant['id']}:digest:2026-W45"
    # A flag-gated workflow switched on by the owner but with its flag off queues nothing.
    on = client.put("/api/v1/platform/workflows", json={"kind": "po_draft", "enabled": True})
    assert on.status_code == 200 and next(w for w in on.json() if w["kind"] == "po_draft")["available"] is False
    sunday = datetime(2026, 11, 1, 18, 0, tzinfo=ZoneInfo("America/Los_Angeles")).astimezone(timezone.utc)
    assert workflows.run_due(db, sunday) == 0
    flags.set_flag(db, restaurant["id"], flags.V2_ORDERS, True)
    db.commit()
    assert workflows.run_due(db, sunday) == 1
    assert client.put("/api/v1/platform/workflows", json={"kind": "po_draft", "enabled": True, "cron": "nonsense"}).status_code == 422
    from test_restaurant_details import _member

    assert _member(client, db).put("/api/v1/platform/workflows", json={"kind": "po_draft", "enabled": False}).status_code == 403


def test_cron_matching_covers_lists_ranges_steps_and_sunday_as_seven():
    m = workflows._cron_matches
    assert m("0 18 * * 0", datetime(2026, 11, 1, 18, 0)) is True  # Sunday
    assert m("0 18 * * 7", datetime(2026, 11, 1, 18, 0)) is True
    assert m("*/15 9-17 * * 1-5", datetime(2026, 11, 2, 9, 45)) is True
    assert m("*/15 9-17 * * 1-5", datetime(2026, 11, 2, 9, 50)) is False
    assert m("0 2 1,15 * *", datetime(2026, 11, 15, 2, 0)) is True
    assert m("bad", datetime(2026, 11, 15, 2, 0)) is False


def test_forecasting_skips_items_with_too_little_history_and_bounds_the_trend():
    history = {date(2026, 10, 27) - timedelta(days=i): 10.0 for i in range(0, 20)}
    assert forecast.predict(history, date(2026, 10, 28), date(2026, 10, 27)) is None  # three same-weekday points only
    history = {date(2026, 10, 27) - timedelta(days=i): (40.0 if i < 14 else 10.0) for i in range(0, 50)}
    pred, lo, hi = forecast.predict(history, date(2026, 10, 28), date(2026, 10, 27))
    # Weighted toward the recent 40s, and the trend factor is capped at 1.5.
    assert 10 < pred <= 60 and lo <= pred <= hi
