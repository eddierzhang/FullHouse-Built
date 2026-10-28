"""Connections: the providers on offer, what this restaurant has connected, CSV uploads with a
mapping preview, the inbound address and its allow-list, OAuth, syncing now, and pushing an
invoice to accounting. Everything here needs the `v2_connect` flag; the webhooks don't."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.deps import current_user, manager
from app.auth.events import deny
from app.auth.models import OWNER, User
from app.config import settings
from app.connectors import csv_adapter, inbound, oauth
from app.connectors.crypto import encrypt
from app.connectors.models import Connection, ConnectorProvider
from app.db.session import get_db
from app.platform import flags, jobs
from app.platform.models import WebhookInbox
from app.restaurant import profile
from app.restaurant.inventory import scope_for

router = APIRouter(prefix="/api/v1/connect", tags=["connect"])
#: The mail provider's webhook and the OAuth return: reached without a session, checked by secret or signed state.
public_router = APIRouter(prefix="/api/v1/connect", tags=["connect"])


def _scope(db: Session, user: User, request: Request | None = None) -> str:
    restaurant_id = scope_for(db, user)
    if restaurant_id is None:
        raise HTTPException(409, "Finish setup first.")
    if not flags.enabled(db, restaurant_id, flags.V2_CONNECT):
        raise HTTPException(404, "Connections aren't switched on for this restaurant.")
    return restaurant_id


# --- what is on offer, and what is connected ---------------------------------------------------


class ProviderOut(BaseModel):
    provider: str
    kind: str
    label: str
    auth: str
    capabilities: list[str]
    #: Enabled on the site and, for OAuth, configured with an app id.
    available: bool
    notes: str | None


class ConnectionOut(BaseModel):
    id: str
    provider: str
    kind: str
    status: str
    last_sync_at: datetime | None
    error: str | None
    settings: dict


class InboundOut(BaseModel):
    address: str | None
    allowed_senders: list[str]
    last_refused_sender: str | None


class ConnectOut(BaseModel):
    providers: list[ProviderOut]
    connections: list[ConnectionOut]
    inbound: InboundOut
    csv_profiles: list[dict]


def _connection_out(c: Connection) -> ConnectionOut:
    public = {k: v for k, v in (c.settings or {}).items() if k not in ("allowed_senders",)}
    return ConnectionOut(id=c.id, provider=c.provider, kind=c.kind, status=c.status, last_sync_at=c.last_sync_at, error=c.error, settings=public)


def _inbound_out(db: Session, restaurant_id: str) -> InboundOut:
    restaurant = db.get(profile.Restaurant, restaurant_id)
    address = inbound.address_for(db, restaurant) if restaurant else None
    connection = inbound.connection_for(db, restaurant_id)
    return InboundOut(address=address, allowed_senders=list((connection.settings if connection else {}).get("allowed_senders", [])),
                      last_refused_sender=(connection.settings if connection else {}).get("last_refused_sender"))


@router.get("", response_model=ConnectOut)
def overview(user: User = Depends(current_user), db: Session = Depends(get_db)):
    restaurant_id = _scope(db, user)
    from app.connectors.registry import seed_providers

    seed_providers(db)
    providers = []
    for p in db.query(ConnectorProvider).order_by(ConnectorProvider.kind, ConnectorProvider.label):
        available = p.enabled and (p.auth != "oauth" or oauth.configured(p.provider)) and (p.provider != "resend_inbound" or bool(settings.inbound_domain))
        if p.provider == "billcom":
            available = settings.billcom_configured
        providers.append(ProviderOut(provider=p.provider, kind=p.kind, label=p.label, auth=p.auth, capabilities=list(p.capabilities or []),
                                     available=available, notes=p.notes))
    connections = [_connection_out(c) for c in db.query(Connection).filter(Connection.restaurant_id == restaurant_id).order_by(Connection.created_at)]
    from app.sales.models import CsvProfile

    profiles = [{"capability": p.capability, **p.mapping} for p in db.query(CsvProfile).filter(CsvProfile.restaurant_id == restaurant_id)]
    out = ConnectOut(providers=providers, connections=connections, inbound=_inbound_out(db, restaurant_id), csv_profiles=profiles)
    db.commit()
    return out


# --- CSV ----------------------------------------------------------------------------------------


class CsvIn(BaseModel):
    text: str = Field(max_length=5_000_000)
    capability: str | None = None
    #: Our field -> the file's header, when the sniff got it wrong or the file is the restaurant's own.
    mapping: dict[str, str] | None = None
    save_profile: bool = True


class CsvPlanOut(BaseModel):
    format: str
    capability: str
    mapping: dict[str, str]
    headers: list[str]
    row_count: int
    days: int
    first_date: date | None
    last_date: date | None
    preview: list[dict]
    problems: list[dict]
    ok: bool
    fields: list[dict]


def _plan_out(result: csv_adapter.Plan) -> CsvPlanOut:
    dates = sorted({r["date"] for r in result.rows})
    return CsvPlanOut(
        format=result.format, capability=result.capability, mapping=result.mapping, headers=result.headers, row_count=len(result.rows),
        days=len(dates), first_date=dates[0] if dates else None, last_date=dates[-1] if dates else None, preview=result.preview,
        problems=[{"line": p.line, "message": p.message} for p in result.problems], ok=result.ok,
        fields=[{"field": f, "required": r} for f, r in csv_adapter.FIELDS.get(result.capability, ())],
    )


@router.post("/csv/preview", response_model=CsvPlanOut)
def csv_preview(body: CsvIn, user: User = Depends(manager), db: Session = Depends(get_db)):
    restaurant_id = _scope(db, user)
    result = csv_adapter.plan(body.text, body.capability, body.mapping, csv_adapter.profile_for(db, restaurant_id, body.capability))
    return _plan_out(result)


class CsvImportOut(BaseModel):
    written: dict[str, int]
    plan: CsvPlanOut


@router.post("/csv/import", response_model=CsvImportOut)
def csv_import(body: CsvIn, user: User = Depends(manager), db: Session = Depends(get_db)):
    restaurant_id = _scope(db, user)
    result = csv_adapter.plan(body.text, body.capability, body.mapping, csv_adapter.profile_for(db, restaurant_id, body.capability))
    if not result.ok:
        raise HTTPException(422, "The file has problems. Fix them and upload it again." if result.problems else "The file has no rows.")
    written = csv_adapter.carry_out(db, restaurant_id, result, body.save_profile)
    from app.sales import pnl

    for week in sorted({pnl.week_start(r["date"]) for r in result.rows}):
        jobs.enqueue(db, "pnl", f"pnl:{restaurant_id}:{week.isoformat()}", {"restaurant_id": restaurant_id, "week_of": week.isoformat()},
                     restaurant_id=restaurant_id)
    db.commit()
    return CsvImportOut(written=written, plan=_plan_out(result))


# --- inbound mail ------------------------------------------------------------------------------


class InboundIn(BaseModel):
    allowed_senders: list[str] = Field(max_length=50)


@router.get("/inbound", response_model=InboundOut)
def inbound_settings(user: User = Depends(current_user), db: Session = Depends(get_db)):
    restaurant_id = _scope(db, user)
    out = _inbound_out(db, restaurant_id)
    db.commit()
    return out


@router.put("/inbound", response_model=InboundOut)
def set_inbound(body: InboundIn, user: User = Depends(manager), db: Session = Depends(get_db)):
    restaurant_id = _scope(db, user)
    connection = inbound.connection_for(db, restaurant_id, create=True)
    senders = [s.strip().lower() for s in body.allowed_senders if s.strip()][:50]
    connection.settings = {**connection.settings, "allowed_senders": senders}
    db.commit()
    return _inbound_out(db, restaurant_id)


@public_router.post("/inbound/webhook")
async def inbound_webhook(request: Request, db: Session = Depends(get_db)):
    """A delivery from the mail provider. Needs X-Inbound-Secret. Written raw once; documents and an ingest job follow."""
    if not settings.inbound_secret or request.headers.get("x-inbound-secret") != settings.inbound_secret:
        raise HTTPException(403, "Set INBOUND_SECRET and send it as X-Inbound-Secret.")
    raw = await request.body()
    try:
        payload = json.loads(raw) if raw else {}
    except ValueError:
        raise HTTPException(422, "Send JSON.")
    if not isinstance(payload, dict):
        raise HTTPException(422, "Send a JSON object.")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    external_id = str(data.get("email_id") or data.get("message_id") or data.get("id") or hashlib.sha256(raw).hexdigest())[:128]
    row = WebhookInbox(provider="resend_inbound", external_id=external_id, payload={"size": len(raw), "subject": str(data.get("subject", ""))[:200],
                                                                                   "from": str(data.get("from", ""))[:200], "to": str(data.get("to", ""))[:200]})
    db.add(row)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        db.rollback()
        return {"ok": True, "duplicate": True}
    result = inbound.receive(db, payload, external_id)
    row.restaurant_id = result.get("restaurant_id")
    db.commit()
    return {"ok": True, **{k: v for k, v in result.items() if k != "restaurant_id"}}


# --- OAuth -------------------------------------------------------------------------------------


@router.get("/oauth/{provider}/start")
def oauth_start(provider: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Owners connect systems. Returns the provider's page to send the person to."""
    restaurant_id = _scope(db, user)
    if user.role != OWNER:
        raise deny(db, user, request, "Only the owner can connect or disconnect systems.")
    if provider not in oauth.SPECS or not oauth.configured(provider):
        raise HTTPException(404, f"{provider} isn't available on this site.")
    return {"url": oauth.authorize_url(provider, restaurant_id)}


@public_router.get("/oauth/{provider}/callback")
def oauth_callback(provider: str, code: str = "", state: str = "", realmId: str = "", error: str = "", db: Session = Depends(get_db)):
    """The provider sends the person back here; the tokens are stored encrypted and a 90-day backfill is queued."""
    parsed = oauth.read_state(state) if state else None
    if provider not in oauth.SPECS or parsed is None or parsed[0] != provider:
        raise HTTPException(400, "This sign-in link is stale. Start again from Connections.")
    _provider, restaurant_id = parsed
    front = f"{settings.public_url}/#connect" if settings.public_url else "/#connect"
    if error or not code:
        return RedirectResponse(f"{front}?error={provider}")
    tokens = oauth.exchange_code(provider, code)
    if realmId:
        tokens["realmId"] = realmId
    if tokens.get("expires_in") and not tokens.get("expires_at"):
        from datetime import timedelta

        tokens["expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=int(tokens["expires_in"]))).isoformat()
    from app.connectors.registry import seed_providers

    seed_providers(db)
    spec = db.get(ConnectorProvider, provider)
    connection = db.query(Connection).filter(Connection.restaurant_id == restaurant_id, Connection.provider == provider).first()
    if connection is None:
        connection = Connection(restaurant_id=restaurant_id, provider=provider, kind=spec.kind if spec else "pos", settings={})
        db.add(connection)
    connection.credentials_enc = encrypt(tokens)
    connection.status = "active"
    connection.error = None
    db.flush()
    if provider != "qbo":
        jobs.enqueue(db, "sync", f"sync:{provider}:{restaurant_id}", {"provider": provider, "restaurant_id": restaurant_id, "connection_id": connection.id},
                     restaurant_id=restaurant_id)
    from app.audit.models import ChangeLog  # noqa: F401  the connection row itself is kept out of the history (credentials)
    from app.platform import events

    events.emit(db, restaurant_id, "connection.made", "connections", connection.id, {"provider": provider})
    db.commit()
    return RedirectResponse(f"{front}?connected={provider}")


@router.post("/{provider}/sync")
def sync_now(provider: str, user: User = Depends(manager), db: Session = Depends(get_db)):
    restaurant_id = _scope(db, user)
    connection = db.query(Connection).filter(Connection.restaurant_id == restaurant_id, Connection.provider == provider).first()
    if provider != "billcom" and connection is None:
        raise HTTPException(404, f"{provider} isn't connected.")
    job = jobs.enqueue(db, "sync", f"sync:{provider}:{restaurant_id}",
                       {"provider": provider, "restaurant_id": restaurant_id, "connection_id": connection.id if connection else None},
                       restaurant_id=restaurant_id)
    db.commit()
    return {"queued": True, "job_id": job.id}


@router.delete("/connections/{connection_id}")
def disconnect(connection_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    restaurant_id = _scope(db, user)
    if user.role != OWNER:
        raise deny(db, user, request, "Only the owner can connect or disconnect systems.")
    connection = db.get(Connection, connection_id)
    if connection is None or connection.restaurant_id != restaurant_id:
        raise HTTPException(404, "No such connection.")
    connection.status = "revoked"
    connection.credentials_enc = None
    from app.platform import events

    events.emit(db, restaurant_id, "connection.revoked", "connections", connection.id, {"provider": connection.provider})
    db.commit()
    return {"ok": True}


# --- accounting ---------------------------------------------------------------------------------


@router.post("/accounting/push/{invoice_id}")
def push_invoice(invoice_id: str, user: User = Depends(manager), db: Session = Depends(get_db)):
    """Push one checked invoice to QuickBooks as a bill, by hand. Bookkeepers and managers; never automatic."""
    restaurant_id = _scope(db, user)
    from app.prices.models import Invoice

    invoice = db.get(Invoice, invoice_id)
    if invoice is None or invoice.restaurant_id != restaurant_id:
        raise HTTPException(404, "No such invoice.")
    job = jobs.enqueue(db, "sync", f"push:qbo:{invoice.id}", {"provider": "qbo", "restaurant_id": restaurant_id, "push_invoice_id": invoice.id},
                       restaurant_id=restaurant_id)
    db.commit()
    return {"queued": True, "job_id": job.id, "already_pushed": bool(invoice.external_refs.get("qbo"))}
