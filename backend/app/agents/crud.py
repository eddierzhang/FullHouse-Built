from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.agents.models import AgentAction, AgentDefinition, AgentEvent, AgentRun


def _now() -> datetime:
    return datetime.now(timezone.utc)


def list_definitions(db: Session) -> list[AgentDefinition]:
    return list(db.scalars(select(AgentDefinition).order_by(AgentDefinition.role.desc(), AgentDefinition.name)))


def get_definition(db: Session, definition_id: str) -> AgentDefinition | None:
    return db.get(AgentDefinition, definition_id)


def get_definition_by_key(db: Session, key: str) -> AgentDefinition | None:
    return db.scalar(select(AgentDefinition).where(AgentDefinition.key == key))


def create_run(
    db: Session,
    agent_definition_id: str,
    *,
    parent_run_id: str | None = None,
    depth: int = 0,
    trigger_type: str = "manual",
    input: str | None = None,
    restaurant_id: str | None = None,
) -> AgentRun:
    """Queue a run for `restaurant_id` -- by default whichever restaurant the
    current work is for (app.restaurant.inventory.site_scope)."""
    from app.restaurant.inventory import site_scope

    run = AgentRun(
        restaurant_id=restaurant_id or site_scope(db),
        agent_definition_id=agent_definition_id,
        parent_run_id=parent_run_id,
        depth=depth,
        trigger_type=trigger_type,
        input=input,
        status="queued",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def get_run(db: Session, run_id: str) -> AgentRun | None:
    return db.get(AgentRun, run_id)


def list_runs(db: Session, restaurant_id: str | None, agent_definition_id: str | None = None) -> list[AgentRun]:
    stmt = select(AgentRun).where(AgentRun.restaurant_id == restaurant_id).order_by(AgentRun.created_at.desc())
    if agent_definition_id:
        stmt = stmt.where(AgentRun.agent_definition_id == agent_definition_id)
    return list(db.scalars(stmt))


def set_run_status(db: Session, run: AgentRun, status: str, **fields) -> AgentRun:
    run.status = status
    for k, v in fields.items():
        setattr(run, k, v)
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def next_event_seq(db: Session, run_id: str) -> int:
    last = db.scalar(
        select(AgentEvent.seq).where(AgentEvent.run_id == run_id).order_by(AgentEvent.seq.desc())
    )
    return (last or 0) + 1


def add_event(db: Session, run_id: str, type: str, payload: dict) -> AgentEvent:
    event = AgentEvent(run_id=run_id, seq=next_event_seq(db, run_id), type=type, payload=payload)
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def list_events(db: Session, run_id: str) -> list[AgentEvent]:
    return list(db.scalars(select(AgentEvent).where(AgentEvent.run_id == run_id).order_by(AgentEvent.seq)))


def create_action(
    db: Session, run_id: str, agent_definition_id: str, action_type: str, payload: dict
) -> AgentAction:
    # A proposal belongs to its run's restaurant: only that restaurant's
    # owners and admins may decide it, and approving it acts only there.
    run = db.get(AgentRun, run_id)
    action = AgentAction(
        restaurant_id=run.restaurant_id if run else None,
        run_id=run_id, agent_definition_id=agent_definition_id, action_type=action_type, payload=payload,
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    return action


def list_actions(db: Session, restaurant_id: str | None, status: str | None = None) -> list[AgentAction]:
    stmt = select(AgentAction).where(AgentAction.restaurant_id == restaurant_id).order_by(AgentAction.created_at.desc())
    if status:
        stmt = stmt.where(AgentAction.status == status)
    return list(db.scalars(stmt))


def get_action(db: Session, action_id: str) -> AgentAction | None:
    return db.get(AgentAction, action_id)


def claim_pending(db: Session, action: AgentAction, status: str) -> bool:
    """Atomically move `action` from pending to `status`; False if it was already decided.

    Checking `action.status` in Python isn't enough: two managers approving
    at once both read "pending" and both apply the effect -- a reorder
    booked twice. A conditional UPDATE lets the database pick one winner:
    the loser's WHERE no longer matches once the winner has written, so it
    changes nothing. Uncommitted, like `mark_action_decided`: if the effect
    then fails, rolling back returns the action to pending.
    """
    claimed = db.execute(
        update(AgentAction)
        .where(AgentAction.id == action.id, AgentAction.status == "pending")
        .values(status=status)
        .execution_options(synchronize_session=False)
    )
    return claimed.rowcount == 1


def mark_action_decided(
    db: Session, action: AgentAction, status: str, applied_result: str | None = None
) -> AgentAction:
    """Stage an approve/reject decision. Deliberately does not commit.

    The caller commits, so that a decision and the domain change it
    authorises share one transaction -- otherwise a failure between the
    two leaves an action marked approved whose effect never landed.
    """
    action.status = status
    action.decided_at = _now()
    action.applied_result = applied_result
    db.add(action)
    return action
