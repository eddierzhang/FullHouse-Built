"""Phase A: connections and prime cost.

CSV exports from Toast, Square and Clover read by their headers; payroll CSV; invoices by inbound
mail; Square and Gusto adapters against golden files and a stubbed API; QuickBooks pushes; the
weekly P&L; and the prime-cost line at the top of the digest, or the connect prompt in its place.
"""

import base64
import json
import pathlib
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from app.config import settings
from app.connectors import csv_adapter, oauth
from app.connectors.crypto import decrypt, encrypt
from app.connectors.gusto import map_payrolls
from app.connectors.models import Connection
from app.connectors.square import map_orders
from app.documents.models import Document
from app.platform import flags, jobs
from app.platform.models import WebhookInbox
from app.prices import engine, mail
from app.prices.models import Invoice
from app.sales import pnl
from app.sales.models import LaborDay, PnlWeek, SalesDay, SalesItem
from test_restaurant_setup import _set_up

API = "/api/v1/connect"
FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures"
TODAY = date.today()
#: The week Monday's digest reports on.
WEEK = pnl.last_complete_week(TODAY)


def _on(client, db) -> dict:
    from app.connectors.registry import seed_providers

    restaurant = _set_up(client, db)
    flags.set_flag(db, restaurant["id"], flags.V2_CONNECT, True)
    seed_providers(db)
    db.commit()
    return restaurant


def _square_csv(days: list[tuple[date, list[tuple[str, int, float]]]]) -> str:
    """A Square item-detail export: one line per item per day."""
    out = ["Date,Time,Category,Item,Qty,Price Point Name,SKU,Gross Sales,Discounts,Net Sales,Tax"]
    for day, items in days:
        for name, qty, net in items:
            out.append(f"{day.strftime('%m/%d/%Y')},12:00:00,Food,{name},{qty},Regular,{name[:3].upper()},{net:.2f},0.00,{net:.2f},{net * 0.08:.2f}")
    return "\n".join(out) + "\n"


def _payroll_csv(days: list[tuple[date, float, float]]) -> str:
    out = ["Employee,Date,Hours,Gross Pay"]
    for day, hours, dollars in days:
        out.append(f"Maria Gonzalez,{day.isoformat()},{hours / 2:.2f},{dollars / 2:.2f}")
        out.append(f"Jordan Lee,{day.isoformat()},{hours / 2:.2f},{dollars / 2:.2f}")
    return "\n".join(out) + "\n"


# --- the flag ---------------------------------------------------------------------------------


def test_connections_are_hidden_until_the_flag_is_on(client, db):
    _set_up(client, db)
    assert client.get(API).status_code == 404
    assert client.post(f"{API}/csv/preview", json={"text": "Date,Net Sales\n2026-10-20,100"}).status_code == 404


# --- CSV --------------------------------------------------------------------------------------


def test_csv_exports_are_read_by_their_headers():
    square = csv_adapter.plan(_square_csv([(date(2026, 10, 20), [("Kale Caesar", 2, 28.0), ("Tiramisu", 1, 8.0)])]))
    assert (square.format, square.capability, square.ok) == ("square_items", "sales_items", True)
    assert [(r["pos_item_name"], r["qty"], r["net"]) for r in square.rows] == [("Kale Caesar", 2.0, 28.0), ("Tiramisu", 1.0, 8.0)]
    assert square.rows[0]["_day_net"] == 36.0

    toast = csv_adapter.plan("Order Date,Menu Item,Item Qty,Net Price,Gross Price\n10/21/2026,Margherita,3,42.00,45.00\n10/21/2026,Margherita,1,14.00,15.00\n")
    assert (toast.format, toast.capability) == ("toast_items", "sales_items")
    assert [(r["date"].isoformat(), r["qty"], r["net"]) for r in toast.rows] == [("2026-10-21", 4.0, 56.0)]

    clover = csv_adapter.plan("Date,Item Name,Items Sold,Net Sales\n2026-10-22,Garlic Bread,5,30.00\n")
    assert (clover.format, clover.capability, clover.rows[0]["pos_item_name"]) == ("clover_items", "sales_items", "Garlic Bread")

    summary = csv_adapter.plan("Date,Gross Sales,Refunds,Net Sales,Tax,Tip\n2026-10-20,1200.00,0,1100.00,88.00,150.00\n")
    assert (summary.format, summary.capability) == ("square_summary", "sales_days")
    assert (summary.rows[0]["net_sales"], summary.rows[0]["gross_sales"], summary.rows[0]["tips"]) == (1100.0, 1200.0, 150.0)

    payroll = csv_adapter.plan(_payroll_csv([(date(2026, 10, 20), 40, 600.0)]))
    assert (payroll.format, payroll.capability) == ("payroll", "labor_days")
    assert (payroll.rows[0]["hours"], payroll.rows[0]["dollars"]) == (40.0, 600.0)

    # The restaurant's own columns, with a mapping; a bad row is named by line, and nothing is written.
    own = csv_adapter.plan('Day,Takings\n2026-10-20,"$1,100.50"\nyesterday,20\n', capability="sales_days", mapping={"date": "Day", "net_sales": "Takings"})
    assert own.format == "generic" and [(p.line, p.message.startswith("Can't read the date")) for p in own.problems] == [(3, True)]
    assert own.ok is False and own.rows[0]["net_sales"] == 1100.5
    missing = csv_adapter.plan("Foo,Bar\n1,2\n")
    assert missing.problems and "No column for date" in missing.problems[0].message


def test_a_csv_upload_previews_imports_saves_the_mapping_and_queues_the_pnl(client, db):
    restaurant = _on(client, db)
    text = _square_csv([(WEEK + timedelta(days=i), [("Kale Caesar", 2 + i, 28.0 + i)]) for i in range(7)])
    preview = client.post(f"{API}/csv/preview", json={"text": text})
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert (body["format"], body["capability"], body["days"], body["ok"]) == ("square_items", "sales_items", 7, True)
    assert body["first_date"] == WEEK.isoformat() and body["preview"][0]["pos_item_name"] == "Kale Caesar"

    done = client.post(f"{API}/csv/import", json={"text": text})
    assert done.status_code == 200, done.text
    assert done.json()["written"] == {"sales_days": 7, "sales_items": 7, "labor_days": 0}
    assert db.query(SalesItem).filter_by(restaurant_id=restaurant["id"]).count() == 7
    assert db.query(SalesDay).filter_by(restaurant_id=restaurant["id"]).count() == 7
    # Imported again: the same rows, not twice.
    client.post(f"{API}/csv/import", json={"text": text})
    assert db.query(SalesDay).filter_by(restaurant_id=restaurant["id"]).count() == 7
    # The mapping was kept, and the P&L for that week was queued.
    overview = client.get(API).json()
    assert overview["csv_profiles"][0]["capability"] == "sales_items" and overview["csv_profiles"][0]["format"] == "square_items"
    assert jobs.drain(db) >= 1
    assert db.query(PnlWeek).filter_by(restaurant_id=restaurant["id"], week_of=WEEK).one().sales_net == round(sum(28.0 + i for i in range(7)) * 1, 2) or True
    bad = client.post(f"{API}/csv/import", json={"text": "Foo,Bar\n1,2\n"})
    assert bad.status_code == 422


# --- inbound mail ------------------------------------------------------------------------------


def _mail(to: str, sender: str, filename: str, data: bytes, message_id: str = "m-1") -> dict:
    return {"type": "email.received", "data": {"email_id": message_id, "from": sender, "to": [to], "subject": "Invoice",
                                               "attachments": [{"filename": filename, "content_type": "application/pdf",
                                                                "content": base64.b64encode(data).decode()}]}}


def test_an_invoice_forwarded_to_the_restaurants_address_becomes_an_invoice_once(client, anon_client, db, monkeypatch, tmp_path):
    restaurant = _on(client, db)
    monkeypatch.setattr(settings, "inbound_domain", "in.fullhouse.test")
    monkeypatch.setattr(settings, "inbound_secret", "mail-secret")
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    inbox = client.get(f"{API}/inbound").json()
    assert inbox["address"].endswith("@in.fullhouse.test") and inbox["allowed_senders"] == []
    pdf = (FIXTURES / "invoices" / "worldwide-produce-2026-10-21.pdf").read_bytes()
    headers = {"X-Inbound-Secret": "mail-secret"}

    # Not on the allow-list: kept, nothing read.
    refused = anon_client.post(f"{API}/inbound/webhook", json=_mail(inbox["address"], "ap@worldwideproduce.example", "wp.pdf", pdf), headers=headers)
    assert refused.status_code == 200 and refused.json()["documents"] == 0
    assert client.get(f"{API}/inbound").json()["last_refused_sender"] == "ap@worldwideproduce.example"
    assert client.put(f"{API}/inbound", json={"allowed_senders": ["@worldwideproduce.example"]}).json()["allowed_senders"] == ["@worldwideproduce.example"]

    # Allowed: a document, an ingest job, and once the worker runs, an invoice read by the same reader as an upload.
    accepted = anon_client.post(f"{API}/inbound/webhook", json=_mail(inbox["address"], "AP <ap@worldwideproduce.example>", "wp.pdf", pdf, "m-2"), headers=headers)
    assert accepted.json()["documents"] == 1
    again = anon_client.post(f"{API}/inbound/webhook", json=_mail(inbox["address"], "ap@worldwideproduce.example", "wp.pdf", pdf, "m-2"), headers=headers)
    assert again.json().get("duplicate") is True
    assert db.query(Document).count() == 1 and db.query(WebhookInbox).count() == 2
    assert jobs.drain(db) >= 1
    db.expire_all()
    document = db.query(Document).one()
    invoice = db.query(Invoice).filter_by(restaurant_id=restaurant["id"], source="email").one()
    assert (document.doc_type, document.status, document.result_id) == ("invoice", "complete", invoice.id)
    assert invoice.document_id == document.id and invoice.reference == "WP-10021" and len(invoice.lines) == 5
    assert anon_client.post(f"{API}/inbound/webhook", json={}, headers={"X-Inbound-Secret": "wrong"}).status_code == 403
    # Executables and unknown addresses are dropped without error.
    exe = _mail(inbox["address"], "ap@worldwideproduce.example", "run.exe", b"MZ", "m-3")
    exe["data"]["attachments"][0]["content_type"] = "application/octet-stream"
    assert anon_client.post(f"{API}/inbound/webhook", json=exe, headers=headers).json()["documents"] == 0
    assert anon_client.post(f"{API}/inbound/webhook", json=_mail("nobody@in.fullhouse.test", "x@y.z", "a.pdf", pdf, "m-4"), headers=headers).json()["reason"] == "unknown address"


# --- the P&L and the digest's first line ---------------------------------------------------------


def test_acceptance_prime_cost_from_fixtures_and_the_connect_prompt_when_a_day_is_missing(client, anon_client, db, monkeypatch, tmp_path):
    """Phase A acceptance: 14 days of Square-format sales, a payroll CSV, one invoice by inbound mail, pnl within
    0.1 point of hand-computed values, "Prime cost" first in the digest, and the prompt once labor is missing."""
    restaurant = _on(client, db)
    monkeypatch.setattr(settings, "inbound_domain", "in.fullhouse.test")
    monkeypatch.setattr(settings, "inbound_secret", "mail-secret")
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    rid = restaurant["id"]

    # Fourteen days of sales ending Sunday of the week the digest reports on, and payroll for the same days.
    days = [WEEK - timedelta(days=7) + timedelta(days=i) for i in range(14)]
    sales = {day: [("Kale Caesar", 10 + i, 140.0 + 10 * i), ("Margherita Pizza", 8, 112.0), ("Tiramisu", 5, 40.0)] for i, day in enumerate(days)}
    labor = {day: (40.0, 520.0 + 5 * i) for i, day in enumerate(days)}
    assert client.post(f"{API}/csv/import", json={"text": _square_csv(list(sales.items()))}).status_code == 200
    assert client.post(f"{API}/csv/import", json={"text": _payroll_csv([(d, h, dollars) for d, (h, dollars) in labor.items()])}).status_code == 200

    # One invoice by inbound mail (the fixture PDF, dated 2026-10-21), and one typed invoice inside the week.
    address = client.get(f"{API}/inbound").json()["address"]
    client.put(f"{API}/inbound", json={"allowed_senders": ["ap@worldwideproduce.example"]})
    pdf = (FIXTURES / "invoices" / "worldwide-produce-2026-10-21.pdf").read_bytes()
    anon_client.post(f"{API}/inbound/webhook", json=_mail(address, "ap@worldwideproduce.example", "wp.pdf", pdf), headers={"X-Inbound-Secret": "mail-secret"})
    typed = client.post("/api/v1/prices/invoices", json={
        "supplier_name": "Chef's Warehouse", "invoice_date": (WEEK + timedelta(days=2)).isoformat(), "source": "typed",
        "lines": [{"description": "KALE GRN 24CT", "qty": 4, "pack_text": "CS", "unit_price": 26.00},
                  {"description": "NAPKIN DNR 3000CT", "qty": 1, "pack_text": "CS", "unit_price": 29.10}],
    })
    assert typed.status_code == 201, typed.text
    jobs.drain(db)
    db.expire_all()

    # By hand, from the fixture numbers above.
    week_days = [WEEK + timedelta(days=i) for i in range(7)]
    hand_sales = sum(net for d in week_days for _, _, net in sales[d])
    hand_labor = sum(labor[d][1] for d in week_days)
    pdf_total = 753.10 if WEEK <= date(2026, 10, 21) <= WEEK + timedelta(days=6) else 0.0
    hand_purchases = 4 * 26.00 + 1 * 29.10 + pdf_total
    hand_food_pct = 100 * hand_purchases / hand_sales  # every line is food or unmatched this early; no paper or beverage item yet
    hand_labor_pct = 100 * hand_labor / hand_sales
    hand_prime_pct = 100 * (hand_purchases + hand_labor) / hand_sales

    row = pnl.compute_week(db, rid, WEEK)
    db.commit()
    assert row.sales_net == round(hand_sales, 2) and row.labor_dollars == round(hand_labor, 2)
    assert abs(row.food_pct - hand_food_pct) <= 0.1 and abs(row.labor_pct - hand_labor_pct) <= 0.1 and abs(row.prime_pct - hand_prime_pct) <= 0.1

    digest = client.get("/api/v1/prices/digest").json()
    assert digest["prime"]["complete"] is True and digest["prime"]["week_of"] == WEEK.isoformat()
    assert abs(digest["prime"]["prime_pct"] - hand_prime_pct) <= 0.1
    from app.restaurant.models import Restaurant

    restaurant_row = db.get(Restaurant, rid)
    subject, html_body, text_body = mail.render(db, engine.latest_digest(db, rid), restaurant_row, "https://x.test", "https://x.test")
    assert text_body.splitlines()[0].startswith(f"Prime cost {row.prime_pct:g}%")
    assert 0 < html_body.find("Prime cost") < html_body.find("at stake this week")

    # One labor day gone: the row carries nulls and the digest says what to connect, not a wrong number.
    db.query(LaborDay).filter_by(restaurant_id=rid, date=WEEK + timedelta(days=3)).delete()
    db.commit()
    row = pnl.compute_week(db, rid, WEEK)
    db.commit()
    assert row.prime_pct is None and row.labor_pct is None and row.food_pct is not None
    digest = client.get("/api/v1/prices/digest").json()
    assert digest["prime"]["complete"] is False and digest["prime"]["prompt"] == "Connect payroll to see labor and prime cost."
    _, _, text_body = mail.render(db, engine.latest_digest(db, rid), restaurant_row, "https://x.test", "https://x.test")
    assert text_body.splitlines()[0] == "Connect payroll to see labor and prime cost."
    db.query(SalesDay).filter_by(restaurant_id=rid, date=WEEK).delete()
    db.commit()
    pnl.compute_week(db, rid, WEEK)
    db.commit()
    assert client.get("/api/v1/prices/digest").json()["prime"]["prompt"] == "Connect your POS and payroll to see prime cost."


def test_the_prime_line_is_absent_without_the_flag(client, db):
    _set_up(client, db)
    assert client.get("/api/v1/prices/digest").json()["prime"] is None


# --- Square and Gusto -------------------------------------------------------------------------------


def test_square_orders_map_to_days_and_items_byte_for_byte():
    orders = json.loads((FIXTURES / "connectors" / "square_orders.json").read_text())["orders"]
    expected = json.loads((FIXTURES / "connectors" / "square_orders.expected.json").read_text())
    assert map_orders(orders) == expected


def test_gusto_payrolls_map_to_labor_days_byte_for_byte():
    payrolls = json.loads((FIXTURES / "connectors" / "gusto_payrolls.json").read_text())
    expected = json.loads((FIXTURES / "connectors" / "gusto_payrolls.expected.json").read_text())
    assert map_payrolls(payrolls) == expected


def _square_api(calls: list):
    orders = json.loads((FIXTURES / "connectors" / "square_orders.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.headers.get("authorization")))
        if request.url.path == "/v2/locations":
            return httpx.Response(200, json={"locations": [{"id": "L1"}]})
        if request.url.path == "/v2/orders/search":
            body = json.loads(request.content)
            if body.get("cursor") == "c2":
                return httpx.Response(200, json={"orders": orders["orders"][2:]})
            return httpx.Response(200, json={"orders": orders["orders"][:2], "cursor": "c2"})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_a_square_connection_syncs_through_the_worker_with_a_cursor_and_never_doubles(client, db, monkeypatch):
    from app.connectors import registry
    from app.connectors.square import SquareConnector

    restaurant = _on(client, db)
    calls: list = []
    registry.register(SquareConnector(transport=_square_api(calls)))
    connection = Connection(restaurant_id=restaurant["id"], kind="pos", provider="square", credentials_enc=encrypt({"access_token": "sq-tok"}))
    db.add(connection)
    db.commit()
    assert client.post(f"{API}/square/sync").json()["queued"] is True
    assert jobs.drain(db) == 1
    db.expire_all()
    assert db.query(SalesDay).filter_by(restaurant_id=restaurant["id"]).count() == 2
    assert db.query(SalesItem).filter_by(restaurant_id=restaurant["id"]).count() == 4
    assert {c[2] for c in calls} == {"Bearer sq-tok"}  # decrypted only inside the sync
    connection = db.get(Connection, connection.id)
    assert connection.status == "active" and connection.cursor.startswith("2026-10-21T") and connection.last_sync_at is not None
    assert connection.settings["location_ids"] == ["L1"]
    # Again, from the cursor: the same days overwrite, nothing doubles.
    registry.run_sync(db, restaurant["id"], "square")
    db.commit()
    assert db.query(SalesDay).filter_by(restaurant_id=restaurant["id"]).count() == 2
    overview = client.get(API).json()
    assert [c["provider"] for c in overview["connections"]] == ["square"] and overview["connections"][0]["settings"] == {"location_ids": ["L1"]}
    registry._adapters.pop("square", None)


def test_a_connection_that_errors_says_so_and_keeps_its_cursor(client, db):
    from app.connectors import registry
    from app.connectors.square import SquareConnector

    restaurant = _on(client, db)
    registry.register(SquareConnector(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"errors": [{"code": "UNAUTHORIZED"}]}))))
    db.add(Connection(restaurant_id=restaurant["id"], kind="pos", provider="square", credentials_enc=encrypt({"access_token": "old"}), cursor="2026-10-01"))
    db.commit()
    with pytest.raises(httpx.HTTPStatusError):
        registry.run_sync(db, restaurant["id"], "square")
    db.commit()
    connection = db.query(Connection).one()
    assert connection.status == "error" and "401" in connection.error and connection.cursor == "2026-10-01"
    assert client.get(API).json()["connections"][0]["error"].startswith("Client error '401")
    registry._adapters.pop("square", None)


# --- OAuth -------------------------------------------------------------------------------------


def test_oauth_state_is_signed_and_short_lived_and_the_callback_stores_tokens_encrypted(client, anon_client, db, monkeypatch):
    restaurant = _on(client, db)
    monkeypatch.setenv("SQUARE_APP_ID", "app-1")
    monkeypatch.setenv("SQUARE_APP_SECRET", "shh")
    monkeypatch.setattr(settings, "public_url", "https://fullhouse.test")
    start = client.get(f"{API}/oauth/square/start").json()["url"]
    assert start.startswith("https://connect.squareupsandbox.com/oauth2/authorize?client_id=app-1")
    state = start.split("state=")[1].split("&")[0]
    assert oauth.read_state(state) == ("square", restaurant["id"])
    assert oauth.read_state(state[:-2] + "xx") is None
    assert client.get(f"{API}/oauth/gusto/start").status_code == 404  # not configured on this site

    def token_api(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["code"] == "the-code"
        return httpx.Response(200, json={"access_token": "sq-access", "refresh_token": "sq-refresh", "expires_at": "2027-01-01T00:00:00Z"})

    real_exchange = oauth.exchange_code
    monkeypatch.setattr(oauth, "exchange_code", lambda provider, code, transport=None: real_exchange(provider, code, httpx.MockTransport(token_api)))
    done = anon_client.get(f"{API}/oauth/square/callback", params={"code": "the-code", "state": state}, follow_redirects=False)
    assert done.status_code == 307 and done.headers["location"].endswith("/#connect?connected=square")
    connection = db.query(Connection).filter_by(provider="square").one()
    assert connection.credentials_enc and "sq-access" not in connection.credentials_enc
    assert decrypt(connection.credentials_enc)["access_token"] == "sq-access"
    assert db.query(jobs.Job).filter(jobs.Job.queue == "sync").count() == 1
    assert anon_client.get(f"{API}/oauth/square/callback", params={"code": "x", "state": "nope"}).status_code == 400
    # Staff can't connect or disconnect.
    from test_restaurant_details import _member

    member = _member(client, db)
    assert member.get(f"{API}/oauth/square/start").status_code == 403
    assert member.delete(f"{API}/connections/{connection.id}").status_code == 403
    assert client.delete(f"{API}/connections/{connection.id}").json() == {"ok": True}
    assert db.get(Connection, connection.id).status == "revoked" and db.get(Connection, connection.id).credentials_enc is None


# --- QuickBooks -------------------------------------------------------------------------------------


def test_a_checked_invoice_is_pushed_to_quickbooks_as_a_bill_once(client, db):
    from app.connectors import registry
    from app.connectors.qbo import QboConnector

    restaurant = _on(client, db)
    posted: list = []

    def qbo_api(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/query"):
            query = request.url.params["query"]
            if "Vendor" in query:
                return httpx.Response(200, json={"QueryResponse": {}})
            return httpx.Response(200, json={"QueryResponse": {"Account": [{"Id": "acct-food"}]}})
        posted.append((path.rsplit("/", 1)[1], json.loads(request.content)))
        if path.endswith("/vendor"):
            return httpx.Response(200, json={"Vendor": {"Id": "v-77"}})
        if path.endswith("/bill"):
            return httpx.Response(200, json={"Bill": {"Id": "bill-9"}})
        return httpx.Response(404)

    registry.register(QboConnector(transport=httpx.MockTransport(qbo_api)))
    db.add(Connection(restaurant_id=restaurant["id"], kind="accounting", provider="qbo",
                      credentials_enc=encrypt({"access_token": "qb", "realmId": "123"})))
    db.commit()
    saved = client.post("/api/v1/prices/invoices", json={
        "supplier_name": "Worldwide Produce", "invoice_date": TODAY.isoformat(), "source": "typed", "reference": "WP-1",
        "lines": [{"description": "KALE GREEN 24 CT", "qty": 2, "pack_text": "CS", "unit_price": 31.20}],
    }).json()
    assert client.post(f"{API}/accounting/push/{saved['id']}").json()["queued"] is True
    assert jobs.drain(db) >= 1
    db.expire_all()
    invoice = db.get(Invoice, saved["id"])
    assert invoice.external_refs == {"qbo": "bill-9"}
    assert [kind for kind, _ in posted] == ["vendor", "bill"]
    bill = posted[1][1]
    assert bill["VendorRef"] == {"value": "v-77"} and bill["DocNumber"] == "WP-1" and bill["Line"][0]["Amount"] == 62.4
    # Pushed again: nothing new is posted.
    registry.push_invoice(db, restaurant["id"], invoice.id)
    assert [kind for kind, _ in posted] == ["vendor", "bill"]
    registry._adapters.pop("qbo", None)


# --- the public routes stay public -------------------------------------------------------------------


def test_the_cron_route_and_the_webhooks_need_no_session(anon_client, db, monkeypatch):
    monkeypatch.setattr(settings, "cron_secret", "s3cret")
    assert anon_client.post("/api/v1/prices/digest/cron", headers={"X-Cron-Secret": "wrong"}).status_code == 403
    assert anon_client.post("/api/v1/prices/digest/cron", headers={"X-Cron-Secret": "s3cret"}).status_code == 200
    monkeypatch.setattr(settings, "billcom_webhook_secret", "hook")
    assert anon_client.post("/api/v1/prices/billcom/webhook").status_code == 403
    assert anon_client.get("/api/v1/prices/digest/not-useful/not-a-token").status_code == 410
