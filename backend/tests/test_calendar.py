"""Phase D: the calendar and the equipment register.

A health permit expiring in twenty days and an equipment invoice with a twelve-month warranty become calendar
proposals (the warranty's carrying the equipment); approved, the daily scan opens a "Coming due" card for the permit
with its date; a proof document closes it; a date moved to yesterday is overdue.
"""

import base64
import importlib.util
import pathlib
from datetime import date, timedelta

from app.calendar import engine
from app.calendar.models import Asset, CalendarItem
from app.platform import flags, jobs
from app.prices import engine as prices
from test_restaurant_setup import _set_up

API = "/api/v1/calendar"
TODAY = date.today()
_spec = importlib.util.spec_from_file_location("fixture_pdf", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "make_fixture_invoices.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
pdf_bytes = _mod.pdf_bytes


def _on(client, db, tmp_path, monkeypatch) -> dict:
    from app.config import settings

    restaurant = _set_up(client, db)
    for flag in (flags.V2_CALENDAR, flags.V2_DOCUMENTS):
        flags.set_flag(db, restaurant["id"], flag, True)
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    db.commit()
    return restaurant


def _upload(client, name: str, data: bytes) -> dict:
    made = client.post("/api/v1/documents", json={"filename": name, "media_type": "application/pdf", "content": base64.b64encode(data).decode()})
    assert made.status_code == 201, made.text
    return made.json()


def _pending(client, kind=None):
    rows = client.get("/api/v1/agents/actions?status=pending").json()
    return [a for a in rows if kind is None or a["action_type"] == kind]


def test_acceptance_permit_and_warranty_to_calendar_to_coming_due_to_proof_to_overdue(client, db, tmp_path, monkeypatch):
    restaurant = _on(client, db, tmp_path, monkeypatch)
    rid = restaurant["id"]
    expires = TODAY + timedelta(days=20)
    permit = pdf_bytes(["City of Austin Health Department", "Food Service Permit", f"Issued: {(TODAY - timedelta(days=345)).isoformat()}",
                        f"Expires: {expires.isoformat()}", "Permit No 44-1021"])
    warranty = pdf_bytes(["Metro Restaurant Equipment", "Invoice 7781", f"Date: {TODAY.isoformat()}", "Equipment: True T-49 reach-in refrigerator",
                          "Serial: 9981-AA", "Warranty: 12 months parts and labor", "Sold by: Metro Restaurant Equipment", "Total 3,850.00"])
    permit_doc = _upload(client, "health-permit.pdf", permit)
    warranty_doc = _upload(client, "fridge-invoice.pdf", warranty)
    assert jobs.drain(db) >= 2
    db.expire_all()
    docs = {d["id"]: d for d in client.get("/api/v1/documents").json()}
    assert docs[permit_doc["id"]]["doc_type"] == "permit" and docs[permit_doc["id"]]["extracted_summary"]["expires_on"] == expires.isoformat()
    assert docs[warranty_doc["id"]]["doc_type"] == "warranty"
    assert docs[warranty_doc["id"]]["extracted_summary"]["expires_on"] == (TODAY + timedelta(days=365)).isoformat()

    proposals = _pending(client, "calendar_item")
    assert len(proposals) == 2
    by_kind = {p["payload"]["kind"]: p for p in proposals}
    assert by_kind["permit"]["payload"]["due_on"] == expires.isoformat() and by_kind["permit"]["payload"]["recurrence"] == "yearly"
    asset = by_kind["warranty"]["payload"]["asset"]
    assert "T-49" in asset["name"] and asset["warranty_until"] == (TODAY + timedelta(days=365)).isoformat()
    for p in proposals:
        approved = client.post(f"/api/v1/agents/actions/{p['id']}/approve")
        assert approved.status_code == 200, approved.text
    assert db.query(CalendarItem).count() == 2 and db.query(Asset).count() == 1
    (fridge,) = db.query(Asset).all()
    assert "T-49" in fridge.name and fridge.warranty_until == TODAY + timedelta(days=365)

    # The daily scan, then the digest: the permit is coming due; the warranty, a year out, is not.
    assert client.post(f"{API}/scan").json()["overdue"] == 0
    digest = client.get("/api/v1/prices/digest").json()
    due = next(s for s in digest["sections"] if s["key"] == "due")
    (card,) = due["cards"]
    permit_item = db.query(CalendarItem).filter_by(kind="permit").one()
    assert card["calendar_item_id"] == permit_item.id and prices.short_date(expires) in card["sentence"] and "due in 20 days" in card["sentence"]
    assert card["action"]["kind"] == "calendar"
    page = client.get(API).json()
    assert [i["kind"] for i in page["items"]] == ["permit", "warranty"] and page["items"][0]["days_left"] == 20
    assert page["assets"][0]["name"] == fridge.name

    # Proof attached: the item is done, the next year's is made, and the card closes.
    proof = client.post(f"{API}/items/{permit_item.id}/proof", json={"filename": "renewed-permit.pdf", "media_type": "application/pdf",
                                                                     "content": base64.b64encode(pdf_bytes(["Food Service Permit", "Renewed"])).decode()})
    assert proof.status_code == 200 and proof.json()["status"] == "done" and proof.json()["proof_document_id"]
    digest = client.get("/api/v1/prices/digest").json()
    assert next(s for s in digest["sections"] if s["key"] == "due")["cards"] == []
    nxt = db.query(CalendarItem).filter_by(kind="permit", status="open").one()
    assert nxt.due_on == expires + timedelta(days=365)

    # A date moved to yesterday is overdue, and so says the page.
    moved = client.patch(f"{API}/items/{nxt.id}", json={"due_on": (TODAY - timedelta(days=1)).isoformat()})
    assert moved.json()["status"] == "overdue" and moved.json()["days_left"] == -1
    digest = client.get("/api/v1/prices/digest").json()
    (card,) = next(s for s in digest["sections"] if s["key"] == "due")["cards"]
    assert "overdue by 1 day" in card["sentence"]


def test_equipment_coming_up_for_service_is_proposed_once(client, db, tmp_path, monkeypatch):
    restaurant = _on(client, db, tmp_path, monkeypatch)
    made = client.post(f"{API}/assets", json={"name": "Hood and ducts", "service_interval_days": 90,
                                              "last_service_on": (TODAY - timedelta(days=80)).isoformat()})
    assert made.status_code == 201
    assert client.post(f"{API}/scan").json()["proposed"] == 1
    assert client.post(f"{API}/scan").json()["proposed"] == 0
    (proposal,) = _pending(client, "calendar_item")
    assert proposal["payload"]["kind"] == "service" and proposal["payload"]["due_on"] == (TODAY + timedelta(days=10)).isoformat()
    assert client.post(f"/api/v1/agents/actions/{proposal['id']}/approve").status_code == 200
    item = db.query(CalendarItem).filter_by(kind="service").one()
    assert item.asset_id == made.json()["id"] and item.recurrence == "90 days"
    # Typed-in dates need no proposal.
    typed = client.post(f"{API}/items", json={"kind": "tax_filing", "title": "Sales tax return", "due_on": (TODAY + timedelta(days=5)).isoformat(), "recurrence": "monthly"})
    assert typed.status_code == 201 and typed.json()["days_left"] == 5
    assert client.post(f"{API}/items", json={"kind": "party", "title": "x", "due_on": TODAY.isoformat()}).status_code == 422


def test_the_calendar_is_hidden_without_the_flag(client, db):
    _set_up(client, db)
    assert client.get(API).status_code == 404
