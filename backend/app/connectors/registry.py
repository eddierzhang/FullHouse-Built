"""Which adapter serves which provider, and how a sync job runs one.

This module decrypts credentials, so it is the only one that imports `crypto.decrypt`,
and it runs in the worker (sync jobs), never in a request.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.connectors.base import Connector, TenantContext
from app.connectors.crypto import decrypt
from app.connectors.models import Connection, ConnectorProvider

logger = logging.getLogger(__name__)

_adapters: dict[str, Connector] = {}
_writers: dict[str, callable] = {}


def register(adapter: Connector) -> Connector:
    _adapters[adapter.provider] = adapter
    return adapter


def adapter_for(provider: str) -> Connector:
    _load()
    try:
        return _adapters[provider]
    except KeyError:
        raise LookupError(f"No connector adapter for {provider!r}") from None


_loaded = False


def _load() -> None:
    """The built-in adapters and writers, once. An adapter registered before (a test's stub) is kept."""
    global _loaded
    if _loaded:
        return
    from app.connectors import writers
    from app.connectors.billcom import BillcomConnector
    from app.connectors.gusto import GustoConnector
    from app.connectors.qbo import QboConnector
    from app.connectors.square import SquareConnector

    for adapter in (BillcomConnector(), SquareConnector(), GustoConnector(), QboConnector()):
        _adapters.setdefault(adapter.provider, adapter)
    _writers.setdefault("sales_days", writers.write_sales_days)
    _writers.setdefault("sales_items", writers.write_sales_items)
    _writers.setdefault("labor_days", writers.write_labor_days)
    _loaded = True


#: The providers the site offers, seeded once. Enabled means an adapter exists here.
PROVIDERS = [
    dict(provider="csv", kind="pos", label="CSV export (Toast, Square, Clover, or your own columns)", auth="none",
         capabilities=["sales_days", "sales_items", "labor_days"], enabled=True,
         notes="Export sales by day and by item, or payroll hours by day; the columns are matched on upload."),
    dict(provider="resend_inbound", kind="email", label="Email invoices in", auth="none",
         capabilities=["invoices", "documents"], enabled=True,
         notes="Forward invoices and documents to the restaurant's own address."),
    dict(provider="square", kind="pos", label="Square", auth="oauth", capabilities=["sales_days", "sales_items", "menu"], enabled=True),
    dict(provider="qbo", kind="accounting", label="QuickBooks Online", auth="oauth", capabilities=["push_invoices"], enabled=True,
         notes="Complete invoices are pushed as bills. Nothing is pulled."),
    dict(provider="gusto", kind="payroll", label="Gusto", auth="oauth", capabilities=["labor_days"], enabled=True),
    dict(provider="billcom", kind="ap", label="Bill.com", auth="api_key", capabilities=["invoices"], enabled=True,
         notes="Bills with their line items are pulled in as invoices. Read-only."),
    dict(provider="clover", kind="pos", label="Clover", auth="oauth", capabilities=["sales_days", "sales_items"], enabled=False),
    dict(provider="toast", kind="pos", label="Toast", auth="partner", capabilities=["sales_days", "sales_items", "menu"], enabled=False,
         notes="Needs Toast's partner program; the adapter is stubbed behind a flag."),
    dict(provider="xero", kind="accounting", label="Xero", auth="oauth", capabilities=["push_invoices"], enabled=False),
]


def seed_providers(db: Session) -> int:
    """Make sure every known provider has its row. Returns how many were added."""
    known = {p.provider for p in db.query(ConnectorProvider)}
    added = 0
    for row in PROVIDERS:
        if row["provider"] not in known:
            db.add(ConnectorProvider(**row))
            added += 1
    if added:
        db.flush()
    return added


def context_for(db: Session, restaurant_id: str, connection: Connection | None) -> TenantContext:
    """The only place a TenantContext with credentials is made: inside the worker's sync."""
    return TenantContext(restaurant_id=restaurant_id, db=db, connection=connection,
                         credentials=decrypt(connection.credentials_enc) if connection else {})


def run_sync(db: Session, restaurant_id: str, provider: str, connection_id: str | None = None,
             until: datetime | None = None) -> int:
    """Pull from one provider for one restaurant until the adapter says it is done. Returns rows written."""
    from app.platform import events

    adapter = adapter_for(provider)
    connection = db.get(Connection, connection_id) if connection_id else (
        db.query(Connection).filter(Connection.restaurant_id == restaurant_id, Connection.provider == provider).first()
    )
    if connection is not None and connection.restaurant_id != restaurant_id:
        raise PermissionError("connection belongs to another restaurant")
    ctx = context_for(db, restaurant_id, connection)
    until = until or datetime.now(timezone.utc)
    cursor = connection.cursor if connection else None
    written = 0
    try:
        for _ in range(1000):
            result = adapter.sync(ctx, cursor, until)
            written += _upsert_normalized(db, restaurant_id, result.capability, result.rows, connection)
            cursor = result.next_cursor
            if result.done:
                break
        if connection is not None:
            connection.cursor = cursor
            connection.status = "active"
            connection.error = None
            connection.last_sync_at = datetime.now(timezone.utc)
    except Exception as exc:
        if connection is not None:
            connection.status = "error"
            connection.error = str(exc)[:1000]
            db.flush()
        raise
    db.flush()
    events.emit(db, restaurant_id, "connector.synced", "connections", connection.id if connection else provider,
                {"provider": provider, "rows": written})
    if written and adapter.capabilities and "sales_items" in adapter.capabilities:
        events.emit(db, restaurant_id, "sales_items.synced", "connections", connection.id if connection else provider, {"rows": written})
    return written


def push_invoice(db: Session, restaurant_id: str, invoice_id: str) -> str:
    """Push one invoice to the restaurant's accounting connection (QuickBooks). Returns the bill id."""
    from app.prices.models import Invoice

    invoice = db.get(Invoice, invoice_id)
    if invoice is None or invoice.restaurant_id != restaurant_id:
        raise LookupError("no such invoice")
    connection = db.query(Connection).filter(Connection.restaurant_id == restaurant_id, Connection.provider == "qbo",
                                             Connection.status == "active").first()
    if connection is None:
        raise RuntimeError("QuickBooks isn't connected.")
    adapter = adapter_for("qbo")
    ctx = context_for(db, restaurant_id, connection)
    bill_id = adapter.push_invoice(db, ctx.credentials, invoice)
    connection.last_sync_at = datetime.now(timezone.utc)
    from app.platform import events

    events.emit(db, restaurant_id, "invoice.pushed", "supplier_invoices", invoice.id, {"provider": "qbo", "bill_id": bill_id})
    return bill_id


#: capability -> writer. Each writer upserts on its unique key, so a re-run with the same cursor changes nothing.


def writer(capability: str):
    def register_writer(fn):
        _writers[capability] = fn
        return fn

    return register_writer


def _upsert_normalized(db: Session, restaurant_id: str, capability: str, rows: list[dict], connection: Connection | None) -> int:
    if not rows:
        return 0
    if capability == "mixed":
        # One adapter call brought several kinds of row (Square: days and items); each goes to its writer.
        total = 0
        by_kind: dict[str, list[dict]] = {}
        for row in rows:
            by_kind.setdefault(row.get("_capability", ""), []).append({k: v for k, v in row.items() if k != "_capability"})
        for kind, kind_rows in by_kind.items():
            total += _upsert_normalized(db, restaurant_id, kind, kind_rows, connection)
        return total
    _load()
    fn = _writers.get(capability)
    if fn is None:
        # The adapter wrote the typed tables itself (Bill.com does, through app.prices.billcom).
        return sum(int(r.get("added", 0)) for r in rows if isinstance(r, dict))
    return fn(db, restaurant_id, rows, connection)
