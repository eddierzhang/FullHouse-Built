"""Scheduled agent workflows, per restaurant, on its own clock.

Each `workflows` row names a kind and a cron. Once a minute the runner looks for rows whose cron
matches the current minute in the restaurant's timezone and enqueues the matching job with a
singleton key of (restaurant, kind, period), so a restart in the same minute doesn't double it.
Default rows are made when setup finishes (and backfilled at startup for restaurants from before).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.platform.models import Workflow

logger = logging.getLogger(__name__)

#: kind -> (default cron, on by default, the flag it needs)
DEFAULTS = {
    "digest": ("30 5 * * 1", True, None),          # rebuild before the 06:00 mail
    "pnl": ("0 5 * * 1", True, "v2_connect"),
    "calendar_scan": ("0 7 * * *", True, "v2_calendar"),
    "recipe_onboarding": ("15 7 * * 1", False, "v2_documents"),
    "forecast": ("0 2 * * *", False, "v2_orders"),
    "po_draft": ("0 18 * * 0", False, "v2_orders"),
    "prep_sheet": ("0 16 * * *", False, "v2_orders"),
}


def ensure_defaults(db: Session, restaurant_id: str) -> int:
    have = {w.kind for w in db.query(Workflow).filter(Workflow.restaurant_id == restaurant_id)}
    added = 0
    for kind, (cron, enabled, _flag) in DEFAULTS.items():
        if kind not in have:
            db.add(Workflow(restaurant_id=restaurant_id, kind=kind, cron=cron, enabled=enabled))
            added += 1
    if added:
        db.flush()
    return added


def _cron_matches(expression: str, moment: datetime) -> bool:
    fields = expression.split()
    if len(fields) != 5:
        return False

    def ok(field: str, value: int, dow: bool = False) -> bool:
        for part in field.split(","):
            if part == "*":
                return True
            if part.startswith("*/") and part[2:].isdigit():
                if value % int(part[2:]) == 0:
                    return True
                continue
            if "-" in part:
                lo, hi = part.split("-", 1)
                if lo.isdigit() and hi.isdigit() and int(lo) <= value <= int(hi):
                    return True
                continue
            if part.isdigit() and int(part) == value:
                return True
            if dow and part.isdigit() and int(part) == 7 and value == 0:
                return True
        return False

    minute, hour, dom, month, dow = fields
    weekday = (moment.weekday() + 1) % 7  # cron: 0 = Sunday
    return ok(minute, moment.minute) and ok(hour, moment.hour) and ok(dom, moment.day) and ok(month, moment.month) and ok(dow, weekday, dow=True)


def period_key(kind: str, moment: datetime) -> str:
    if kind in ("digest", "pnl", "recipe_onboarding", "po_draft"):
        return moment.strftime("%G-W%V")
    return moment.date().isoformat()


def _local(db: Session, restaurant_id: str, moment: datetime) -> datetime:
    from app.restaurant.models import Restaurant

    restaurant = db.get(Restaurant, restaurant_id)
    try:
        return moment.astimezone(ZoneInfo((restaurant.timezone if restaurant else None) or "UTC"))
    except Exception:
        return moment


def enqueue_for(db: Session, workflow: Workflow, moment: datetime | None = None, local: datetime | None = None) -> str | None:
    """Queue the job a workflow stands for, now. Returns the job id, or None when its flag is off or one is already
    queued for the period. Dates in the job are the restaurant's own day, not UTC's."""
    from app.platform import flags, jobs

    _cron, _default, flag = DEFAULTS.get(workflow.kind, ("", False, None))
    if flag and not flags.enabled(db, workflow.restaurant_id, flag):
        return None
    moment = moment or datetime.now(timezone.utc)
    local = local or _local(db, workflow.restaurant_id, moment)
    today = local.date().isoformat()
    key = f"workflow:{workflow.restaurant_id}:{workflow.kind}:{period_key(workflow.kind, local)}"
    payload = {"restaurant_id": workflow.restaurant_id, "today": today}
    queue = {"digest": "detect", "pnl": "pnl", "forecast": "forecast", "po_draft": "workflow", "prep_sheet": "workflow",
             "recipe_onboarding": "workflow", "calendar_scan": "workflow"}[workflow.kind]
    if queue == "workflow":
        payload["kind"] = workflow.kind
    job, created = jobs.enqueue_new(db, queue, key, payload, restaurant_id=workflow.restaurant_id)
    if not created:
        return None
    workflow.last_run_at = moment
    db.flush()
    return job.id


def run_due(db: Session, now: datetime | None = None) -> int:
    """Called once a minute: enqueue every enabled workflow whose cron matches this minute on its restaurant's clock."""
    from app.restaurant.models import Restaurant

    now = now or datetime.now(timezone.utc)
    queued = 0
    zones: dict[str, str] = {r.id: r.timezone or "UTC" for r in db.query(Restaurant.id, Restaurant.timezone)}
    for workflow in db.query(Workflow).filter(Workflow.enabled.is_(True)):
        try:
            local = now.astimezone(ZoneInfo(zones.get(workflow.restaurant_id, "UTC")))
        except Exception:
            local = now
        if _cron_matches(workflow.cron, local):
            if enqueue_for(db, workflow, now, local):
                queued += 1
    if queued:
        db.commit()
    return queued


def tick() -> int:
    """The scheduler's minute job."""
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        return run_due(db)
    except Exception:
        logger.exception("workflow tick failed")
        return 0
    finally:
        db.close()
