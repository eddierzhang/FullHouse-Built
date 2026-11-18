"""The Margin & Labor Engine's time-based work, checked by the scheduler's minute tick beside the workflows.

Each check queues a `derive` job and each job is idempotent on its own key (a supplier and delivery date, a
business date), so a tick that fires twice, or late, changes nothing. No workflow rows are added.

    forecast_daily, prep_sheet    04:30 on the restaurant's clock
    order_draft                   from 4 hours before each supplier's order cutoff
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.margin_labor.business import business_date, zone
from app.margin_labor.models import SupplierSchedule
from app.platform import flags, jobs
from app.restaurant.models import Restaurant

logger = logging.getLogger(__name__)
NIGHTLY = (4, 30)


def run_due(db: Session, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    queued = 0
    for restaurant in db.query(Restaurant).filter(Restaurant.setup_completed_at.isnot(None)):
        rid = restaurant.id
        if not flags.any_margin_labor(db, rid):
            continue
        local = now.astimezone(zone(restaurant.timezone))
        if flags.enabled(db, rid, flags.ML_ORDERING):
            from app.margin_labor import ordering

            if (local.hour, local.minute) == NIGHTLY:
                day = business_date(now, restaurant.timezone).isoformat()
                for job in ("forecast_daily", "prep_sheet"):
                    queued += _queue(db, rid, job, f"{job}:{day}", {"business_date": day})
            by_supplier: dict[str, list] = {}
            for row in db.query(SupplierSchedule).filter(SupplierSchedule.restaurant_id == rid):
                by_supplier.setdefault(row.supplier_id, []).append(row)
            for supplier_id, rows in by_supplier.items():
                ahead = ordering.upcoming(rows, local)
                if ahead and ahead[0][0] - local <= timedelta(hours=ordering.DRAFT_LEAD_HOURS):
                    queued += _queue(db, rid, "order_draft", f"order_draft:{supplier_id}:{ahead[0][1].isoformat()}", {"supplier_id": supplier_id})
        queued += _extra(db, restaurant, now, local)
    if queued:
        db.commit()
    return queued


def _extra(db: Session, restaurant, now: datetime, local: datetime) -> int:
    """Module D's timed work: the labor forecast and requirements, nightly with the item forecast."""
    queued = 0
    if flags.enabled(db, restaurant.id, flags.ML_LABOR) and (local.hour, local.minute) == NIGHTLY:
        day = business_date(now, restaurant.timezone).isoformat()
        queued += _queue(db, restaurant.id, "forecast_labor", f"forecast_labor:{day}", {"business_date": day})
    return queued


def _queue(db: Session, restaurant_id: str, job: str, key: str, payload: dict) -> int:
    _, made = jobs.enqueue_new(db, "derive", f"derive:{restaurant_id}:{key}", {"restaurant_id": restaurant_id, "job": job, **payload}, restaurant_id=restaurant_id)
    return 1 if made else 0


def tick(db: Session) -> int:
    try:
        return run_due(db)
    except Exception:
        logger.exception("margin and labor tick failed")
        db.rollback()
        return 0
