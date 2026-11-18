"""Bill.com (v3 API): bills, with their line items, pulled in as invoices.

One Bill.com organisation per site, named by BILLCOM_* in the environment
(app.config). The client signs in, pages through bills with their line items
and vendor names, and `sync` saves each as an invoice -- once: a bill's Bill.com
id is kept as `Invoice.external_id`, so a second pull, or the same bill arriving
by webhook, changes nothing.

Read-only. Nothing here creates, approves or pays anything in Bill.com, and
there is no method on the client that could.

This client is written to the v3 gateway as documented
(https://developer.bill.com, "Connect v3"): sandbox at gateway.stage.bill.com.
It has run against a stub only, not the real sandbox -- see README.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.prices import engine, schemas
from app.prices.models import Invoice
from app.restaurant.models import Supplier

logger = logging.getLogger(__name__)

GATEWAYS = {
    "sandbox": "https://gateway.stage.bill.com/connect/v3",
    "production": "https://gateway.prod.bill.com/connect/v3",
}
PAGE_SIZE = 100


class BillcomError(RuntimeError):
    """Bill.com said no, or isn't set up. The message is for the person."""


@dataclass
class Bill:
    external_id: str
    vendor_id: str | None
    vendor_name: str | None
    invoice_number: str | None
    invoice_date: date | None
    amount: float | None
    lines: list[schemas.LineIn] = field(default_factory=list)


def gateway() -> str:
    return GATEWAYS.get(settings.billcom_environment, GATEWAYS["sandbox"])


class BillcomClient:
    """A signed-in session against one organisation. `transport` is for tests."""

    def __init__(self, transport: httpx.BaseTransport | None = None):
        if not settings.billcom_configured:
            raise BillcomError("Bill.com isn't connected on this site. Set BILLCOM_USERNAME, BILLCOM_PASSWORD, "
                               "BILLCOM_ORG_ID and BILLCOM_DEV_KEY.")
        self._http = httpx.Client(base_url=gateway(), timeout=30, transport=transport)
        self._session_id: str | None = None
        self._vendors: dict[str, str | None] = {}

    # -- sign in ----------------------------------------------------------------------------

    def login(self) -> None:
        response = self._http.post("/login", json={
            "username": settings.billcom_username, "password": settings.billcom_password,
            "organizationId": settings.billcom_org_id, "devKey": settings.billcom_dev_key,
        })
        if response.status_code >= 400:
            raise BillcomError(f"Bill.com refused the sign-in ({response.status_code}). Check the credentials.")
        self._session_id = response.json().get("sessionId")
        if not self._session_id:
            raise BillcomError("Bill.com signed in but gave no session. Try again.")

    def _headers(self) -> dict[str, str]:
        if self._session_id is None:
            self.login()
        return {"sessionId": self._session_id or "", "devKey": settings.billcom_dev_key or ""}

    def _get(self, path: str, **params) -> dict:
        response = self._http.get(path, params=params, headers=self._headers())
        if response.status_code == 401:
            self.login()
            response = self._http.get(path, params=params, headers=self._headers())
        if response.status_code >= 400:
            raise BillcomError(f"Bill.com returned {response.status_code} for {path}.")
        return response.json()

    # -- reading ----------------------------------------------------------------------------

    def vendor_name(self, vendor_id: str | None) -> str | None:
        if not vendor_id:
            return None
        if vendor_id not in self._vendors:
            try:
                self._vendors[vendor_id] = self._get(f"/vendors/{vendor_id}").get("name")
            except BillcomError:
                self._vendors[vendor_id] = None
        return self._vendors[vendor_id]

    def bills(self, since: date | None = None) -> list[Bill]:
        """Every bill, newest first, with its line items as printed in Bill.com."""
        out: list[Bill] = []
        page = None
        while True:
            params: dict = {"max": PAGE_SIZE, "sort": "invoice.invoiceDate:desc"}
            if since:
                params["filters"] = f"invoice.invoiceDate:gte:{since.isoformat()}"
            if page:
                params["page"] = page
            body = self._get("/bills", **params)
            for raw in body.get("results", []):
                out.append(self._bill(raw))
            page = body.get("nextPage")
            if not page:
                break
        return out

    def _bill(self, raw: dict) -> Bill:
        invoice = raw.get("invoice") or {}
        day = None
        if invoice.get("invoiceDate"):
            try:
                day = date.fromisoformat(str(invoice["invoiceDate"])[:10])
            except ValueError:
                day = None
        lines = []
        for item in raw.get("billLineItems") or []:
            description = str(item.get("description") or item.get("itemName") or "").strip()
            try:
                qty = float(item.get("quantity") or 1)
                price = float(item.get("price") if item.get("price") is not None else item.get("amount") or 0)
                amount = float(item["amount"]) if item.get("amount") is not None else None
            except (TypeError, ValueError):
                continue
            if not description or qty <= 0:
                continue
            lines.append(schemas.LineIn(description=description[:200], qty=qty, unit_price=price, extended=amount))
        amount = raw.get("amount")
        return Bill(
            external_id=str(raw.get("id")), vendor_id=raw.get("vendorId"), vendor_name=self.vendor_name(raw.get("vendorId")),
            invoice_number=(str(invoice.get("invoiceNumber") or "")[:60] or None), invoice_date=day,
            amount=float(amount) if amount is not None else None, lines=lines,
        )


# --- into invoices -------------------------------------------------------------------------


def _supplier_for(db: Session, restaurant_id: str | None, name: str) -> Supplier:
    supplier = db.query(Supplier).filter(Supplier.restaurant_id == restaurant_id, Supplier.name.ilike(name)).first()
    if supplier is None:
        supplier = Supplier(restaurant_id=restaurant_id, name=name[:255])
        db.add(supplier)
        db.flush()
    return supplier


def sync(db: Session, restaurant_id: str | None, client: BillcomClient | None = None, today: date | None = None) -> schemas.BillcomSyncOut:
    """Pull bills and save the new ones as invoices. Safe to run as often as you like."""
    today = today or date.today()
    client = client or BillcomClient()
    bills = client.bills(since=today - engine.timedelta(weeks=engine.WINDOW_WEEKS))
    known = {
        external_id for (external_id,) in db.query(Invoice.external_id)
        .filter(Invoice.restaurant_id == restaurant_id, Invoice.external_id.isnot(None))
    }
    added = skipped = asked = 0
    for bill in bills:
        if bill.external_id in known or not bill.lines or bill.invoice_date is None or bill.invoice_date > today:
            skipped += 1
            continue
        supplier = _supplier_for(db, restaurant_id, bill.vendor_name or "Bill.com vendor")
        _invoice, questions = engine.ingest_invoice(
            db, restaurant_id, supplier, bill.invoice_date, bill.lines, source="billcom",
            reference=bill.invoice_number, today=today, total=bill.amount, external_id=bill.external_id,
        )
        known.add(bill.external_id)
        added += 1
        asked += sum(1 for q in questions if q.status == engine.OPEN)
    if added:
        engine.build_digest(db, restaurant_id, today)
    db.flush()
    return schemas.BillcomSyncOut(pulled=len(bills), added=added, skipped=skipped, questions_asked=asked)


def last_synced_at(db: Session, restaurant_id: str | None) -> datetime | None:
    row = (
        db.query(Invoice.created_at).filter(Invoice.restaurant_id == restaurant_id, Invoice.source == "billcom")
        .order_by(Invoice.created_at.desc()).first()
    )
    if row is None:
        return None
    created = row[0]
    return created if created.tzinfo else created.replace(tzinfo=timezone.utc)
