"""Feature flags for the owner, and the ops page for whoever runs the site."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.agents.models import AgentAction, AgentRun
from app.auth.deps import current_user
from app.auth.events import deny
from app.auth.models import OWNER, User
from app.config import settings
from app.connectors.models import Connection
from app.db.session import get_db
from app.platform import flags, jobs
from app.platform.models import FAILED, Job
from app.prices.models import InvoiceLine
from app.restaurant.inventory import scope_for

router = APIRouter(prefix="/api/v1/platform", tags=["platform"])


class FlagsOut(BaseModel):
    flags: dict[str, bool]
    descriptions: dict[str, str]


class FlagIn(BaseModel):
    flag: str
    enabled: bool


@router.get("/flags", response_model=FlagsOut)
def read_flags(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return FlagsOut(flags=flags.all_for(db, scope_for(db, user)), descriptions=flags.FLAGS)


@router.put("/flags", response_model=FlagsOut)
def write_flag(body: FlagIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Owners switch a phase on or off for their restaurant."""
    if user.role != OWNER:
        raise deny(db, user, request, "Only the owner can switch features on or off.")
    restaurant_id = scope_for(db, user)
    if restaurant_id is None:
        raise HTTPException(409, "Finish setup first.")
    try:
        flags.set_flag(db, restaurant_id, body.flag, body.enabled)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return FlagsOut(flags=flags.all_for(db, restaurant_id), descriptions=flags.FLAGS)


def superuser(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
    if user.email.lower() not in settings.superuser_emails:
        raise deny(db, user, request, "The ops page is for the people running the site (SUPERUSER_EMAILS).")
    return user


class OpsOut(BaseModel):
    queues: list[dict]
    failed_jobs: list[dict]
    extraction_confidence: dict
    proposals_pending: list[dict]
    agent_cost_today: list[dict]
    connector_sync_lag: list[dict]


@router.get("/ops", response_model=OpsOut)
def ops(user: User = Depends(superuser), db: Session = Depends(get_db)):
    """Plain tables, no charts: the queues, extraction confidence, proposals waiting, agent cost, sync lag."""
    now = datetime.now(timezone.utc)
    failed = [
        {"id": j.id, "queue": j.queue, "key": j.singleton_key, "attempts": j.attempts, "error": (j.error or "")[:300],
         "finished_at": j.finished_at}
        for j in db.query(Job).filter(Job.status == FAILED).order_by(Job.finished_at.desc()).limit(50)
    ]
    buckets = {"under_0_5": 0, "0_5_to_0_8": 0, "0_8_and_up": 0, "unknown": 0}
    for (confidence,) in db.query(InvoiceLine.confidence).filter(InvoiceLine.confidence.isnot(None)).limit(5000):
        key = "under_0_5" if confidence < 0.5 else "0_5_to_0_8" if confidence < 0.8 else "0_8_and_up"
        buckets[key] += 1
    buckets["unknown"] = db.query(InvoiceLine).filter(InvoiceLine.confidence.is_(None)).count()
    pending = [
        {"restaurant_id": rid, "pending": count}
        for rid, count in db.query(AgentAction.restaurant_id, func.count(AgentAction.id))
        .filter(AgentAction.status == "pending").group_by(AgentAction.restaurant_id)
    ]
    since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cost = [
        {"restaurant_id": rid, "runs": runs, "cost_usd": round(total or 0, 4)}
        for rid, runs, total in db.query(AgentRun.restaurant_id, func.count(AgentRun.id), func.sum(AgentRun.cost_usd))
        .filter(AgentRun.created_at >= since).group_by(AgentRun.restaurant_id)
    ]
    lag = [
        {"provider": c.provider, "restaurant_id": c.restaurant_id, "status": c.status,
         "lag_minutes": int((now - c.last_sync_at).total_seconds() // 60) if c.last_sync_at else None, "error": c.error}
        for c in db.query(Connection).order_by(Connection.provider)
    ]
    return OpsOut(queues=jobs.queue_stats(db), failed_jobs=failed, extraction_confidence=buckets,
                  proposals_pending=pending, agent_cost_today=cost, connector_sync_lag=lag)


@router.get("/files/{key:path}")
def read_file(key: str, expires: int, sig: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """A stored file, through a signed link that expires after fifteen minutes and only for the restaurant's own."""
    from app import storage

    if not storage.verify(key, expires, sig):
        raise HTTPException(410, "This link has expired.")
    owner = key.split("/", 1)[0]
    if owner != (scope_for(db, user) or "site"):
        raise HTTPException(404, "No such file.")
    data = storage.get(key)
    if data is None:
        raise HTTPException(404, "No such file.")
    media = "application/pdf" if key.endswith(".pdf") else "image/jpeg" if key.endswith((".jpg", ".jpeg")) else "image/png" if key.endswith(".png") else "application/octet-stream"
    return Response(content=data, media_type=media)
