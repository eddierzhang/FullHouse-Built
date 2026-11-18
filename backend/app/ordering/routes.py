"""Supplier calendars, autopilot limits and decisions, and the preview of what autopilot would have sent."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import current_user, manager
from app.auth.models import User
from app.db.session import get_db
from app.platform import flags
from app.restaurant.inventory import scope_for

router = APIRouter(prefix="/api/v1/ordering", tags=["ordering"])


def _scope(db: Session, user: User) -> str:
    """The signed-in user's restaurant, with this feature switched on for it."""
    restaurant_id = scope_for(db, user)
    if restaurant_id is None:
        raise HTTPException(404, "Set up your restaurant first.")
    if not flags.enabled(db, restaurant_id, flags.V3_AUTOPILOT):
        raise HTTPException(404, "Ordering autopilot isn't switched on for this restaurant (Profile, Features).")
    return restaurant_id


import re  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

from pydantic import BaseModel, Field  # noqa: E402

from app.auth.models import OWNER  # noqa: E402
from app.ordering import autopilot, pars, policy  # noqa: E402
from app.ordering.models import PASSED, AutopilotDecision, AutopilotPolicy, SupplierCalendar  # noqa: E402
from app.restaurant.models import InventoryItem, PurchaseOrder, Supplier  # noqa: E402


def _supplier(db: Session, restaurant_id: str, supplier_id: str) -> Supplier:
    supplier = db.query(Supplier).filter(Supplier.restaurant_id == restaurant_id, Supplier.id == supplier_id).first()
    if supplier is None:
        raise HTTPException(404, "Supplier not found")
    return supplier


class CalendarIn(BaseModel):
    #: Weekdays orders go in and deliveries arrive, Monday = 0.
    order_days: list[int] = Field(default_factory=list, max_length=7)
    cutoff_time: str = Field(default="14:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    delivery_days: list[int] = Field(default_factory=list, max_length=7)
    lead_time_days: int | None = Field(default=None, ge=0, le=30)
    minimum_dollars: float | None = Field(default=None, ge=0, le=100_000)
    channel: str = Field(default="email", pattern="^(email|portal|phone)$")
    order_email: str | None = Field(default=None, max_length=254)


def _one_address(value: str | None) -> str | None:
    """A single plain email address, or nothing. No lists, no names, nothing a mail client or server would read
    as a second recipient or a header."""
    text = (value or "").strip()
    if not text:
        return None
    if not re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)+", text):
        raise HTTPException(422, "Give one email address for orders, like orders@supplier.com.")
    return text


class PolicyIn(BaseModel):
    enabled: bool = False
    per_order_cap: float = Field(default=300, gt=0, le=100_000)
    weekly_cap: float = Field(default=900, gt=0, le=500_000)
    max_price_rise_pct: float = Field(default=10, ge=0, le=100)
    max_qty_multiple: float = Field(default=1.5, ge=1, le=10)
    min_history: int = Field(default=4, ge=1, le=8)
    #: Orders are drafted three hours before the cutoff, so a longer wait could not be honoured.
    cancel_window_minutes: int = Field(default=30, ge=5, le=150)


class PauseIn(BaseModel):
    paused: bool


class ParIn(BaseModel):
    #: The par to hold, in the item's unit; null goes back to the par worked out from sales.
    par: float | None = Field(default=None, ge=0, le=1_000_000)


def _calendar_out(c: SupplierCalendar | None) -> dict:
    if c is None:
        return CalendarIn().model_dump()
    return {"order_days": sorted(c.order_days or []), "cutoff_time": c.cutoff_time, "delivery_days": sorted(c.delivery_days or []),
            "lead_time_days": c.lead_time_days, "minimum_dollars": c.minimum_dollars, "channel": c.channel, "order_email": c.order_email}


def _policy_out(p: AutopilotPolicy | None) -> dict:
    if p is None:
        return {**PolicyIn().model_dump(), "consecutive_failures": 0, "paused_reason": None}
    return {"enabled": p.enabled, "per_order_cap": p.per_order_cap, "weekly_cap": p.weekly_cap, "max_price_rise_pct": p.max_price_rise_pct,
            "max_qty_multiple": p.max_qty_multiple, "min_history": p.min_history, "cancel_window_minutes": p.cancel_window_minutes,
            "consecutive_failures": p.consecutive_failures, "paused_reason": p.paused_reason}


def _decision_out(db: Session, d: AutopilotDecision, names: dict[str, str]) -> dict:
    order = db.query(PurchaseOrder).filter(PurchaseOrder.restaurant_id == d.restaurant_id, PurchaseOrder.id == d.po_id).first()
    return {"id": d.id, "supplier_id": d.supplier_id, "supplier_name": names.get(d.supplier_id or "", "A supplier"), "outcome": d.outcome,
            "rules": d.rules, "total": d.total, "send_after": d.send_after, "decided_at": d.decided_at, "sent_at": d.sent_at,
            "cancelled_by": d.cancelled_by, "order_id": order.id if order else None,
            "expected_on": order.expected_on if order else None, "lines": len(order.lines) if order else None}


@router.get("/overview")
def overview(user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Every supplier with its order days and limits, what is waiting to send, and what autopilot did lately."""
    restaurant_id = _scope(db, user)
    suppliers = db.query(Supplier).filter(Supplier.restaurant_id == restaurant_id).order_by(Supplier.name).all()
    names = {s.id: s.name for s in suppliers}
    decisions = (db.query(AutopilotDecision).filter(AutopilotDecision.restaurant_id == restaurant_id)
                 .order_by(AutopilotDecision.decided_at.desc()).limit(30).all())
    out = [_decision_out(db, d, names) for d in decisions]
    return {
        "paused": autopilot.is_paused(db, restaurant_id), "can_send": autopilot.mail.can_send() or autopilot.settings.demo_mode,
        "is_owner": user.role == OWNER, "now": datetime.now(timezone.utc),
        "suppliers": [{"id": s.id, "name": s.name, "lead_time_days": s.lead_time_days,
                       "calendar": _calendar_out(autopilot.calendar_for(db, restaurant_id, s.id)),
                       "policy": _policy_out(autopilot.policy_for(db, restaurant_id, s.id)),
                       "order_email": autopilot.order_email(db, restaurant_id, s.id),
                       "outlook": autopilot.outlook(db, restaurant_id, s),
                       "sent_this_week": autopilot.sent_this_week(db, restaurant_id, s.id, datetime.now(timezone.utc))} for s in suppliers],
        "waiting": [d for d in out if d["outcome"] == PASSED], "recent": [d for d in out if d["outcome"] != PASSED],
    }


@router.put("/suppliers/{supplier_id}/calendar")
def set_calendar(supplier_id: str, body: CalendarIn, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """When this supplier takes orders and delivers, their minimum, and how they take orders."""
    restaurant_id = _scope(db, user)
    supplier = _supplier(db, restaurant_id, supplier_id)
    if any(not 0 <= day <= 6 for day in [*body.order_days, *body.delivery_days]):
        raise HTTPException(422, "Days are 0 (Monday) to 6 (Sunday).")
    address = _one_address(body.order_email)  # refused before anything is written
    row = autopilot.calendar_for(db, restaurant_id, supplier.id)
    if row is None:
        row = SupplierCalendar(restaurant_id=restaurant_id, supplier_id=supplier.id)
        db.add(row)
    row.order_days, row.delivery_days = sorted(set(body.order_days)), sorted(set(body.delivery_days))
    row.cutoff_time, row.lead_time_days, row.minimum_dollars = body.cutoff_time, body.lead_time_days, body.minimum_dollars
    row.channel, row.order_email = body.channel, address
    db.commit()
    return _calendar_out(row)


@router.put("/suppliers/{supplier_id}/policy")
def set_policy(supplier_id: str, body: PolicyIn, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """The limits autopilot works inside for this supplier. Only an owner may set them or switch it on."""
    restaurant_id = _scope(db, user)
    if user.role != OWNER:
        raise HTTPException(403, "Only an owner can set autopilot limits or switch it on.")
    supplier = _supplier(db, restaurant_id, supplier_id)
    if body.weekly_cap < body.per_order_cap:
        raise HTTPException(422, "The weekly limit can't be less than the limit for one order.")
    row = autopilot.policy_for(db, restaurant_id, supplier.id)
    if row is None:
        row = AutopilotPolicy(restaurant_id=restaurant_id, supplier_id=supplier.id)
        db.add(row)
    if body.enabled and not row.enabled:
        row.consecutive_failures, row.paused_reason = 0, None
    for name, value in body.model_dump().items():
        setattr(row, name, value)
    row.updated_by = user.email
    db.commit()
    return _policy_out(row)


@router.post("/pause")
def pause(body: PauseIn, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """The pause-all switch. Any manager can stop autopilot; only an owner can start it again, as only an
    owner can switch it on. Nothing goes out while it is paused."""
    restaurant_id = _scope(db, user)
    if not body.paused and user.role != OWNER:
        raise HTTPException(403, "Only an owner can resume autopilot.")
    autopilot.set_paused(db, restaurant_id, body.paused, user.email)
    db.commit()
    return {"paused": body.paused}


@router.post("/decisions/{decision_id}/cancel")
def cancel_send(decision_id: str, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Stop an order before it goes. It stays as a draft under On order."""
    restaurant_id = _scope(db, user)
    decision = db.query(AutopilotDecision).filter(AutopilotDecision.restaurant_id == restaurant_id, AutopilotDecision.id == decision_id).first()
    if decision is None:
        raise HTTPException(404, "Nothing waiting under that id")
    try:
        autopilot.cancel(db, decision, user.email)
    except ValueError as err:
        raise HTTPException(409, str(err)) from err
    db.commit()
    return {"id": decision.id, "outcome": decision.outcome}


@router.post("/suppliers/{supplier_id}/preview")
def preview(supplier_id: str, body: PolicyIn, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """What these limits would have done with this supplier's last eight weeks of invoices. Changes nothing."""
    restaurant_id = _scope(db, user)
    supplier = _supplier(db, restaurant_id, supplier_id)
    limits = policy.Limits(enabled=True, paused=False, per_order_cap=body.per_order_cap, weekly_cap=body.weekly_cap,
                           max_price_rise_pct=body.max_price_rise_pct, max_qty_multiple=body.max_qty_multiple, min_history=body.min_history)
    return autopilot.preview(db, restaurant_id, supplier, limits)


@router.get("/pars")
def read_pars(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    """Each item's use a day and the level it is ordered up to."""
    return pars.table(db, _scope(db, user))


@router.put("/pars/{item_id}")
def set_par(item_id: str, body: ParIn, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Pin a par by hand, or (with null) go back to the par worked out from sales."""
    restaurant_id = _scope(db, user)
    item = db.query(InventoryItem).filter(InventoryItem.restaurant_id == restaurant_id, InventoryItem.id == item_id).first()
    if item is None:
        raise HTTPException(404, "Inventory item not found")
    item.par_pinned = body.par is not None
    item.par = body.par
    if body.par is None:
        pars.recompute(db, restaurant_id)
    db.commit()
    return next(r for r in pars.table(db, restaurant_id) if r["inventory_item_id"] == item.id)


@router.post("/run")
def run_now(user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Do now what the job does every five minutes: pars, drafts due, decisions, and anything due to send."""
    restaurant_id = _scope(db, user)
    result = autopilot.run(db, restaurant_id)
    db.commit()
    return result
