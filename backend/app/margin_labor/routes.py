"""The Margin & Labor Engine's shared data: importing order-level sales, punches and payouts, and saying how
fresh each source is. Module pages (Menu costs, Usage, Ordering and prep, Labor, Channels) add their own routes
as each phase lands. The tenant always comes from the session."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth.deps import current_user, manager
from app.auth.models import User
from app.db.session import get_db
from app.margin_labor import csv_import
from app.margin_labor.models import ChannelPayout, IntervalActual, LaborPunch, SalesOrder
from app.platform import flags, jobs
from app.restaurant.inventory import scope_for

router = APIRouter(prefix="/api/v1/margin-labor", tags=["margin-labor"])

#: How old each source may be before a page that rests on it says so (spec section 4).
FRESHNESS_HOURS = {"orders": 36, "punches": 36}


def _scope(db: Session, user: User) -> str:
    restaurant_id = scope_for(db, user)
    if restaurant_id is None:
        raise HTTPException(404, "Set up your restaurant first.")
    if not flags.any_margin_labor(db, restaurant_id):
        raise HTTPException(404, "None of the margin and labor modules is switched on for this restaurant (Profile, Features).")
    return restaurant_id


class ImportIn(BaseModel):
    kind: str = Field(pattern="^(orders|punches|payouts)$")
    text: str = Field(min_length=1, max_length=40_000_000)


@router.post("/data/import")
def import_csv(body: ImportIn, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Import one CSV export. Read-only for the source, and safe to send twice: every row is matched on its own
    id, so the same file again changes nothing. Rows that can't be read are left out and listed."""
    restaurant_id = _scope(db, user)
    result = csv_import.IMPORTERS[body.kind](db, restaurant_id, body.text)
    db.commit()
    jobs.drain(db, queues=("derive",), limit=400)  # small imports show at once; a large one finishes in the worker
    db.commit()
    return result.as_dict()


@router.get("/data/status")
def data_status(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """What has been imported, how far it reaches, and whether each source is older than its limit."""
    restaurant_id = _scope(db, user)
    now = datetime.now(timezone.utc)

    def source(model, moment, name: str) -> dict:
        count, first, last, synced = db.query(func.count(model.id), func.min(moment), func.max(moment), func.max(model.updated_at)).filter(
            model.restaurant_id == restaurant_id).one()
        limit = FRESHNESS_HOURS.get(name)
        stale = bool(count and limit and synced is not None and _aware(synced) < now - timedelta(hours=limit))
        return {"rows": count, "first": first, "last": last, "last_import_at": synced, "stale": stale, "limit_hours": limit}

    orders = source(SalesOrder, SalesOrder.opened_at, "orders")
    days = db.query(func.count(func.distinct(SalesOrder.business_date))).filter(SalesOrder.restaurant_id == restaurant_id).scalar() or 0
    rolled = db.query(func.count(func.distinct(IntervalActual.business_date))).filter(IntervalActual.restaurant_id == restaurant_id).scalar() or 0
    return {"orders": {**orders, "business_dates": days, "rolled_up_dates": rolled},
            "punches": source(LaborPunch, LaborPunch.clock_in, "punches"),
            "payouts": source(ChannelPayout, ChannelPayout.paid_at, "payouts"),
            "modules": {flag: flags.enabled(db, restaurant_id, flag) for flag in flags.ML_FLAGS}}


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)
