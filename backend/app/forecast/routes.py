"""The next week's forecast by item, prep sheets, and the restaurant's scheduled workflows."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.deps import current_user, manager
from app.auth.events import deny
from app.auth.models import OWNER, User
from app.db.session import get_db
from app.forecast import engine as forecast
from app.forecast.models import Forecast, PrepSheet
from app.platform import flags, workflows
from app.platform.models import Workflow
from app.restaurant.inventory import scope_for

router = APIRouter(prefix="/api/v1", tags=["forecast"])


def _scope(db: Session, user: User, flag: str | None = flags.V2_ORDERS) -> str:
    restaurant_id = scope_for(db, user)
    if restaurant_id is None:
        raise HTTPException(409, "Finish setup first.")
    if flag and not flags.enabled(db, restaurant_id, flag):
        raise HTTPException(404, "This isn't switched on for this restaurant.")
    return restaurant_id


class ForecastRow(BaseModel):
    pos_item_id: str
    pos_item_name: str
    date: date
    qty_pred: float
    qty_lo: float
    qty_hi: float


class ForecastOut(BaseModel):
    rows: list[ForecastRow]
    accuracy_last_week: float | None
    computed_at: datetime | None


@router.get("/forecast", response_model=ForecastOut)
def get_forecast(user: User = Depends(current_user), db: Session = Depends(get_db)):
    restaurant_id = _scope(db, user)
    today = date.today()
    rows = (db.query(Forecast).filter(Forecast.restaurant_id == restaurant_id, Forecast.date > today, Forecast.date <= today + timedelta(days=7))
            .order_by(Forecast.date, Forecast.pos_item_name).all())
    return ForecastOut(rows=[ForecastRow(pos_item_id=r.pos_item_id, pos_item_name=r.pos_item_name, date=r.date, qty_pred=r.qty_pred, qty_lo=r.qty_lo, qty_hi=r.qty_hi) for r in rows],
                       accuracy_last_week=forecast.accuracy_last_week(db, restaurant_id, today),
                       computed_at=max((r.computed_at for r in rows), default=None))


@router.post("/forecast/run", response_model=ForecastOut)
def run_forecast_now(user: User = Depends(manager), db: Session = Depends(get_db)):
    restaurant_id = _scope(db, user)
    forecast.run(db, restaurant_id)
    db.commit()
    return get_forecast(user, db)


class PrepSheetOut(BaseModel):
    id: str
    date: date
    status: str
    lines: list[dict]
    proposal_id: str | None


@router.get("/prep-sheets", response_model=list[PrepSheetOut])
def list_prep_sheets(user: User = Depends(current_user), db: Session = Depends(get_db)):
    restaurant_id = _scope(db, user)
    rows = db.query(PrepSheet).filter(PrepSheet.restaurant_id == restaurant_id).order_by(PrepSheet.date.desc()).limit(30).all()
    return [PrepSheetOut(id=s.id, date=s.date, status=s.status, lines=s.lines, proposal_id=s.proposal_id) for s in rows]


@router.get("/prep-sheets/{sheet_id}", response_model=PrepSheetOut)
def get_prep_sheet(sheet_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    restaurant_id = _scope(db, user)
    sheet = db.get(PrepSheet, sheet_id)
    if sheet is None or sheet.restaurant_id != restaurant_id:
        raise HTTPException(404, "No such prep sheet.")
    return PrepSheetOut(id=sheet.id, date=sheet.date, status=sheet.status, lines=sheet.lines, proposal_id=sheet.proposal_id)


@router.post("/prep-sheets/{sheet_id}/printed", response_model=PrepSheetOut)
def mark_printed(sheet_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    restaurant_id = _scope(db, user)
    sheet = db.get(PrepSheet, sheet_id)
    if sheet is None or sheet.restaurant_id != restaurant_id:
        raise HTTPException(404, "No such prep sheet.")
    sheet.status = "printed"
    db.commit()
    return PrepSheetOut(id=sheet.id, date=sheet.date, status=sheet.status, lines=sheet.lines, proposal_id=sheet.proposal_id)


class WorkflowOut(BaseModel):
    id: str
    kind: str
    cron: str
    enabled: bool
    available: bool
    last_run_at: datetime | None


class WorkflowIn(BaseModel):
    kind: str
    enabled: bool
    cron: str | None = None


def _workflow_out(db: Session, w: Workflow) -> WorkflowOut:
    _cron, _default, flag = workflows.DEFAULTS.get(w.kind, ("", False, None))
    return WorkflowOut(id=w.id, kind=w.kind, cron=w.cron, enabled=w.enabled, available=not flag or flags.enabled(db, w.restaurant_id, flag), last_run_at=w.last_run_at)


@router.get("/platform/workflows", response_model=list[WorkflowOut])
def list_workflows(user: User = Depends(current_user), db: Session = Depends(get_db)):
    restaurant_id = _scope(db, user, None)
    workflows.ensure_defaults(db, restaurant_id)
    db.commit()
    rows = db.query(Workflow).filter(Workflow.restaurant_id == restaurant_id).all()
    order = list(workflows.DEFAULTS)
    return [_workflow_out(db, w) for w in sorted(rows, key=lambda w: order.index(w.kind) if w.kind in order else 99)]


@router.put("/platform/workflows", response_model=list[WorkflowOut])
def set_workflow(body: WorkflowIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    restaurant_id = _scope(db, user, None)
    if user.role != OWNER:
        raise deny(db, user, request, "Only the owner changes the schedules.")
    if body.kind not in workflows.DEFAULTS:
        raise HTTPException(422, f"Unknown workflow. One of: {', '.join(workflows.DEFAULTS)}.")
    workflows.ensure_defaults(db, restaurant_id)
    row = db.query(Workflow).filter(Workflow.restaurant_id == restaurant_id, Workflow.kind == body.kind).one()
    row.enabled = body.enabled
    if body.cron:
        if len(body.cron.split()) != 5:
            raise HTTPException(422, "A schedule is five cron fields, e.g. '0 18 * * 0' for Sunday 6pm.")
        row.cron = body.cron.strip()
    db.commit()
    return list_workflows(user, db)


@router.post("/platform/workflows/{kind}/run")
def run_workflow_now(kind: str, user: User = Depends(manager), db: Session = Depends(get_db)):
    restaurant_id = _scope(db, user, None)
    if kind not in workflows.DEFAULTS:
        raise HTTPException(422, "Unknown workflow.")
    workflows.ensure_defaults(db, restaurant_id)
    row = db.query(Workflow).filter(Workflow.restaurant_id == restaurant_id, Workflow.kind == kind).one()
    job_id = workflows.enqueue_for(db, row)
    db.commit()
    if job_id is None:
        raise HTTPException(409, "That workflow's feature isn't switched on for this restaurant.")
    return {"queued": True, "job_id": job_id}
