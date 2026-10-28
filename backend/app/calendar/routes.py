"""The calendar: one list sorted by date, proof attached by upload, and the equipment register. A list, not a grid."""

from __future__ import annotations

import base64
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import storage
from app.auth.deps import current_user, manager
from app.auth.models import User
from app.calendar import engine
from app.calendar.models import DONE, KINDS, OPEN, OVERDUE, Asset, CalendarItem, Referral
from app.db.session import get_db
from app.documents.models import Document
from app.platform import flags
from app.restaurant.inventory import scope_for

router = APIRouter(prefix="/api/v1/calendar", tags=["calendar"])


def _scope(db: Session, user: User) -> str:
    restaurant_id = scope_for(db, user)
    if restaurant_id is None:
        raise HTTPException(409, "Finish setup first.")
    if not flags.enabled(db, restaurant_id, flags.V2_CALENDAR):
        raise HTTPException(404, "The calendar isn't switched on for this restaurant.")
    return restaurant_id


class ItemOut(BaseModel):
    id: str
    kind: str
    title: str
    due_on: date
    recurrence: str | None
    status: str
    days_left: int
    source_document_id: str | None
    proof_document_id: str | None
    asset_id: str | None
    note: str | None


class AssetOut(BaseModel):
    id: str
    name: str
    category: str | None
    vendor: str | None
    purchased_on: date | None
    warranty_until: date | None
    service_interval_days: int | None
    last_service_on: date | None


class CalendarOut(BaseModel):
    items: list[ItemOut]
    assets: list[AssetOut]


def _item(i: CalendarItem, today: date) -> ItemOut:
    return ItemOut(id=i.id, kind=i.kind, title=i.title, due_on=i.due_on, recurrence=i.recurrence, status=i.status, days_left=(i.due_on - today).days,
                   source_document_id=i.source_document_id, proof_document_id=i.proof_document_id, asset_id=i.asset_id, note=i.note)


def _asset(a: Asset) -> AssetOut:
    return AssetOut(id=a.id, name=a.name, category=a.category, vendor=a.vendor, purchased_on=a.purchased_on, warranty_until=a.warranty_until,
                    service_interval_days=a.service_interval_days, last_service_on=a.last_service_on)


@router.get("", response_model=CalendarOut)
def calendar(user: User = Depends(current_user), db: Session = Depends(get_db)):
    restaurant_id = _scope(db, user)
    today = date.today()
    for item in db.query(CalendarItem).filter(CalendarItem.restaurant_id == restaurant_id, CalendarItem.status == OPEN, CalendarItem.due_on < today):
        item.status = OVERDUE
    db.commit()
    items = db.query(CalendarItem).filter(CalendarItem.restaurant_id == restaurant_id).order_by(CalendarItem.due_on).all()
    open_first = sorted(items, key=lambda i: (i.status == DONE, i.due_on))
    assets = db.query(Asset).filter(Asset.restaurant_id == restaurant_id).order_by(Asset.name).all()
    return CalendarOut(items=[_item(i, today) for i in open_first], assets=[_asset(a) for a in assets])


class ItemIn(BaseModel):
    kind: str
    title: str = Field(max_length=200)
    due_on: date
    recurrence: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=500)


@router.post("/items", response_model=ItemOut, status_code=201)
def add_item(body: ItemIn, user: User = Depends(manager), db: Session = Depends(get_db)):
    """A date the owner knows about without a document: typed in, no proposal needed."""
    restaurant_id = _scope(db, user)
    if body.kind not in KINDS:
        raise HTTPException(422, f"Kind is one of: {', '.join(KINDS)}.")
    item = CalendarItem(restaurant_id=restaurant_id, kind=body.kind, title=body.title.strip(), due_on=body.due_on, recurrence=body.recurrence,
                        status=OVERDUE if body.due_on < date.today() else OPEN, note=body.note)
    db.add(item)
    db.flush()
    from app.prices import engine as prices

    prices.build_digest(db, restaurant_id)
    db.commit()
    return _item(item, date.today())


class DueIn(BaseModel):
    due_on: date


@router.patch("/items/{item_id}", response_model=ItemOut)
def set_due(item_id: str, body: DueIn, user: User = Depends(manager), db: Session = Depends(get_db)):
    restaurant_id = _scope(db, user)
    item = db.get(CalendarItem, item_id)
    if item is None or item.restaurant_id != restaurant_id:
        raise HTTPException(404, "No such item.")
    item.due_on = body.due_on
    if item.status != DONE:
        item.status = OVERDUE if body.due_on < date.today() else OPEN
    from app.prices import engine as prices

    prices.build_digest(db, restaurant_id)
    db.commit()
    return _item(item, date.today())


class ProofIn(BaseModel):
    filename: str = Field(max_length=255)
    media_type: str = Field(max_length=80)
    content: str


@router.post("/items/{item_id}/proof", response_model=ItemOut)
def attach_proof(item_id: str, body: ProofIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The renewal, the report, the receipt: stored as a document, and the item is done (and the next one made)."""
    restaurant_id = _scope(db, user)
    item = db.get(CalendarItem, item_id)
    if item is None or item.restaurant_id != restaurant_id:
        raise HTTPException(404, "No such item.")
    try:
        data = base64.b64decode(body.content, validate=False)
    except Exception as exc:
        raise HTTPException(422, "The file didn't decode.") from exc
    if not data or len(data) > 15 * 1024 * 1024:
        raise HTTPException(422, "The file is empty or over 15 MB.")
    key = storage.new_key(restaurant_id, "proof", body.filename)
    storage.put(key, data, body.media_type)
    document = Document(restaurant_id=restaurant_id, doc_type=item.kind if item.kind in ("license", "permit", "inspection", "warranty") else "other",
                        type_confidence=1.0, file_key=key, filename=body.filename, media_type=body.media_type, source="upload", status="complete",
                        note=f"Proof for: {item.title}", result_table="calendar_items", result_id=item.id)
    db.add(document)
    db.flush()
    engine.attach_proof(db, item, document.id)
    from app.prices import engine as prices

    prices.build_digest(db, restaurant_id)
    db.commit()
    return _item(item, date.today())


class AssetIn(BaseModel):
    name: str = Field(max_length=200)
    category: str | None = Field(default=None, max_length=60)
    vendor: str | None = Field(default=None, max_length=200)
    purchased_on: date | None = None
    warranty_until: date | None = None
    service_interval_days: int | None = Field(default=None, ge=1, le=3650)
    last_service_on: date | None = None


@router.post("/assets", response_model=AssetOut, status_code=201)
def add_asset(body: AssetIn, user: User = Depends(manager), db: Session = Depends(get_db)):
    restaurant_id = _scope(db, user)
    asset = Asset(restaurant_id=restaurant_id, **body.model_dump())
    db.add(asset)
    db.commit()
    return _asset(asset)


@router.patch("/assets/{asset_id}", response_model=AssetOut)
def edit_asset(asset_id: str, body: AssetIn, user: User = Depends(manager), db: Session = Depends(get_db)):
    restaurant_id = _scope(db, user)
    asset = db.get(Asset, asset_id)
    if asset is None or asset.restaurant_id != restaurant_id:
        raise HTTPException(404, "No such equipment.")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(asset, key, value)
    db.commit()
    return _asset(asset)


@router.post("/scan")
def scan_now(user: User = Depends(manager), db: Session = Depends(get_db)):
    restaurant_id = _scope(db, user)
    out = engine.scan(db, restaurant_id)
    from app.prices import engine as prices

    prices.build_digest(db, restaurant_id)
    db.commit()
    return out


class ReferralIn(BaseModel):
    email: str = Field(max_length=254)


@router.post("/referrals", status_code=201)
def refer(body: ReferralIn, user: User = Depends(manager), db: Session = Depends(get_db)):
    """An owner refers another restaurant: recorded, so a sign-up with that address can be thanked."""
    restaurant_id = scope_for(db, user)
    if restaurant_id is None:
        raise HTTPException(409, "Finish setup first.")
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(422, "Give an email address.")
    existing = db.query(Referral).filter(Referral.restaurant_id == restaurant_id, Referral.referred_email == email).first()
    if existing is None:
        db.add(Referral(restaurant_id=restaurant_id, referred_email=email))
        db.commit()
    return {"ok": True, "email": email}


@router.get("/referrals")
def referrals(user: User = Depends(manager), db: Session = Depends(get_db)):
    restaurant_id = scope_for(db, user)
    rows = db.query(Referral).filter(Referral.restaurant_id == restaurant_id).order_by(Referral.created_at.desc()).all()
    return [{"email": r.referred_email, "status": r.status, "created_at": r.created_at} for r in rows]
