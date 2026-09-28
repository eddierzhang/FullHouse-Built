import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.agents import crud, executor, scheduler, schemas
from app.agents.appliers import ApplyError, apply_action, is_appliable, missing_inputs
from app.agents.broker import TERMINAL, broker
from app.audit.context import Actor, actor_context, get_actor
from app.auth.deps import current_user, manager
from app.auth.models import User
from app.db.session import get_db
from app.restaurant.inventory import scope_for

#: How long an idle stream waits before sending a comment to keep the
#: connection open through proxies that time out quiet responses.
HEARTBEAT_SECONDS = 15

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


def _run_for(db: Session, user: User, run_id: str):
    """A run of the user's own restaurant; another's reads as missing."""
    run = crud.get_run(db, run_id)
    if run is None or run.restaurant_id != scope_for(db, user):
        raise HTTPException(404, "Run not found")
    return run


def _action_for(db: Session, user: User, action_id: str):
    """A proposal of the user's own restaurant; another's reads as missing."""
    action = crud.get_action(db, action_id)
    if action is None or action.restaurant_id != scope_for(db, user):
        raise HTTPException(404, "Action not found")
    return action


def _claim(db: Session, action, status: str) -> None:
    """Take the decision on `action`, or refuse if someone else just did.

    The status check before this reads a snapshot; this is the one that
    counts (see crud.claim_pending). On SQLite a simultaneous decision can
    also surface as "database is locked" -- the same situation, so the
    same answer.
    """
    try:
        claimed = crud.claim_pending(db, action, status)
    except OperationalError as exc:
        db.rollback()
        if "locked" not in str(exc).lower():
            raise
        claimed = False
    if not claimed:
        db.rollback()
        raise HTTPException(409, "Someone else has just decided this proposal. Refresh to see what they chose.")


def _definition_out(defn, tz) -> schemas.AgentDefinitionOut:
    out = schemas.AgentDefinitionOut.model_validate(defn)
    if defn.enabled and defn.schedule_cron:
        try:
            out.next_run_at = scheduler.next_fire_time(defn.schedule_cron, tz=tz)
        except scheduler.InvalidSchedule:
            out.next_run_at = None
    return out


@router.get("/runtime")
def runtime(db: Session = Depends(get_db)):
    """What the agents are running on right now, for the UI's status line."""
    from app.agents.providers import get_provider
    from app.config import settings

    # A misconfigured or missing AI mustn't take this down: every page reads
    # it, and everything but the agent itself works without one.
    try:
        provider = get_provider()
        provider_key, model, ai_problem = provider.key, provider.resolve_model("claude-opus-5"), None
    except Exception as exc:  # noqa: BLE001 - reported to the UI instead
        provider_key, model, ai_problem = "unavailable", None, str(exc)
    return {
        "provider": provider_key,
        "model": model,
        # Why the agent can't run right now; null when it can.
        "ai_problem": ai_problem,
        "scheduler_running": scheduler.is_running(),
        "max_concurrent_runs": executor.MAX_CONCURRENT_RUNS,
        "configured_provider": settings.llm_provider,
        "demo_mode": settings.demo_mode,
        # The clock agent schedules run on: the restaurant's, once set up.
        "schedule_timezone": getattr(scheduler.agent_timezone(db), "key", "UTC"),
        "demo_resets_at": scheduler.next_fire_time(settings.demo_reset_cron) if settings.demo_mode else None,
    }


@router.get("/definitions", response_model=list[schemas.AgentDefinitionOut])
def list_definitions(db: Session = Depends(get_db)):
    tz = scheduler.agent_timezone(db)
    return [_definition_out(d, tz) for d in crud.list_definitions(db)]


@router.patch("/definitions/{agent_key}", response_model=schemas.AgentDefinitionOut)
def update_definition(
    agent_key: str, payload: schemas.AgentDefinitionUpdate, _: User = Depends(manager), db: Session = Depends(get_db)
):
    """Schedule, reschedule, pause or resume an agent."""
    defn = crud.get_definition_by_key(db, agent_key)
    if defn is None:
        raise HTTPException(404, f"No agent with key '{agent_key}'")

    fields = payload.model_dump(exclude_unset=True)
    if "schedule_cron" in fields:
        expression = (fields["schedule_cron"] or "").strip() or None
        if expression:
            try:
                scheduler.parse_cron(expression)
            except scheduler.InvalidSchedule as exc:
                raise HTTPException(422, str(exc)) from exc
        defn.schedule_cron = expression
    if fields.get("enabled") is not None:
        defn.enabled = fields["enabled"]

    db.add(defn)
    db.commit()
    db.refresh(defn)
    scheduler.sync_agent_schedules()
    return _definition_out(defn, scheduler.agent_timezone(db))


@router.post("/runs", response_model=schemas.AgentRunOut, status_code=202)
def trigger_run(payload: schemas.TriggerRunRequest, user: User = Depends(manager), db: Session = Depends(get_db)):
    """Queue an agent run and return straight away.

    The run executes on a worker thread; follow it on
    `GET /runs/{id}/stream` or poll `GET /runs/{id}`. Running it inline
    held the request open for the whole run -- minutes, once the Boss
    starts delegating.
    """
    defn = crud.get_definition_by_key(db, payload.agent_key)
    if defn is None:
        raise HTTPException(404, f"No agent with key '{payload.agent_key}'")
    if not defn.enabled:
        raise HTTPException(400, f"Agent '{defn.key}' is disabled")

    run = crud.create_run(
        db, defn.id, trigger_type="manual", input=payload.input, restaurant_id=scope_for(db, user)
    )
    executor.submit(run.id)
    return run


@router.post("/runs/{run_id}/cancel", response_model=schemas.AgentRunOut)
def cancel_run(run_id: str, user: User = Depends(manager), db: Session = Depends(get_db)):
    """Ask a run to stop at its next checkpoint.

    Cooperative: the provider checks between tool rounds, so a model call
    already in flight finishes first rather than being torn off mid-write.
    """
    run = _run_for(db, user, run_id)
    if run.status not in ("queued", "running"):
        raise HTTPException(400, f"Run is already {run.status}")

    executor.request_cancel(run_id)
    db.refresh(run)
    return run


@router.get("/runs/{run_id}/stream")
async def stream_run(
    run_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """Server-sent events for one run.

    Replays what has already been recorded before switching to live, so a
    listener that connects late still sees the whole run.
    """
    run = _run_for(db, user, run_id)

    queue = broker.subscribe(run_id)
    replay = [
        {"type": e.type, "seq": e.seq, "run_id": run_id, "payload": e.payload}
        for e in crud.list_events(db, run_id)
    ]
    finished = run.status in ("succeeded", "failed", "cancelled")

    async def events():
        try:
            for event in replay:
                yield f"data: {json.dumps(event)}\n\n"
            if finished:
                yield f"data: {json.dumps({'type': TERMINAL, 'run_id': run_id})}\n\n"
                return

            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue

                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") == TERMINAL:
                    return
        finally:
            broker.unsubscribe(run_id, queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/runs", response_model=list[schemas.AgentRunOut])
def list_runs(agent_key: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    agent_definition_id = None
    if agent_key:
        defn = crud.get_definition_by_key(db, agent_key)
        if defn is None:
            raise HTTPException(404, f"No agent with key '{agent_key}'")
        agent_definition_id = defn.id
    return crud.list_runs(db, scope_for(db, user), agent_definition_id=agent_definition_id)


@router.get("/runs/{run_id}", response_model=schemas.AgentRunDetailOut)
def get_run(run_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    run = _run_for(db, user, run_id)
    events = crud.list_events(db, run_id)
    child_run_ids = [r.id for r in run.child_runs]
    return schemas.AgentRunDetailOut(
        **schemas.AgentRunOut.model_validate(run).model_dump(),
        events=[schemas.AgentEventOut.model_validate(e) for e in events],
        child_run_ids=child_run_ids,
    )


def _as_out(action) -> schemas.AgentActionOut:
    out = schemas.AgentActionOut.model_validate(action)
    out.appliable = is_appliable(action)
    out.needs_input = missing_inputs(action)
    return out


@router.get("/actions", response_model=list[schemas.AgentActionOut])
def list_actions(status: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [_as_out(a) for a in crud.list_actions(db, scope_for(db, user), status=status)]


@router.post("/actions/{action_id}/approve", response_model=schemas.AgentActionOut)
def approve_action(
    action_id: str,
    payload: schemas.ApproveRequest | None = None,
    user: User = Depends(manager),
    db: Session = Depends(get_db),
):
    """Approve a proposal and carry out its effect.

    The applier, the status change and the audit rows commit together.
    If the effect cannot be applied the whole thing rolls back and the
    action stays pending, so it can be re-approved once the underlying
    problem is fixed.
    """
    action = _action_for(db, user, action_id)
    if action.status != "pending":
        raise HTTPException(400, f"Action already {action.status}")
    _claim(db, action, "approved")

    # Merge and persist the operator's values, so the record shows what was
    # actually approved rather than the incomplete proposal.
    overrides = (payload.overrides if payload else None) or {}
    if overrides:
        action.payload = {**(action.payload or {}), **overrides}

    # The operator is the actor, but the change still traces back to the
    # agent run that proposed it -- the change log records both.
    approver = get_actor()
    attribution = Actor(
        type=approver.type,
        id=approver.id,
        agent_run_id=action.run_id,
        agent_action_id=action.id,
        # The applier looks records up in this restaurant and no other.
        restaurant_id=action.restaurant_id,
    )

    try:
        with actor_context(attribution):
            result = apply_action(db, action)
            crud.mark_action_decided(db, action, "approved", applied_result=result)
            db.commit()
    except ApplyError as exc:
        db.rollback()
        raise HTTPException(422, f"Cannot apply this action: {exc}") from exc
    except Exception:
        db.rollback()
        raise

    db.refresh(action)
    return _as_out(action)


@router.post("/actions/{action_id}/reject", response_model=schemas.AgentActionOut)
def reject_action(action_id: str, user: User = Depends(manager), db: Session = Depends(get_db)):
    action = _action_for(db, user, action_id)
    if action.status != "pending":
        raise HTTPException(400, f"Action already {action.status}")
    _claim(db, action, "rejected")
    crud.mark_action_decided(db, action, "rejected")
    db.commit()
    db.refresh(action)
    return _as_out(action)
