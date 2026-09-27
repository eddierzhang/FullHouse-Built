from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import ChangeLog


def get_change(db: Session, change_id: int) -> ChangeLog | None:
    return db.get(ChangeLog, change_id)


def query_changes(
    db: Session,
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    operation: str | None = None,
    actor_type: str | None = None,
    actor_id: str | None = None,
    agent_run_id: str | None = None,
    agent_action_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[int, list[ChangeLog]]:
    """Filtered page of changes, newest first, plus the unpaginated total."""
    filters = []
    if entity_type:
        filters.append(ChangeLog.entity_type == entity_type)
    if entity_id:
        filters.append(ChangeLog.entity_id == entity_id)
    if operation:
        filters.append(ChangeLog.operation == operation)
    if actor_type:
        filters.append(ChangeLog.actor_type == actor_type)
    if actor_id:
        filters.append(ChangeLog.actor_id == actor_id)
    if agent_run_id:
        filters.append(ChangeLog.agent_run_id == agent_run_id)
    if agent_action_id:
        filters.append(ChangeLog.agent_action_id == agent_action_id)
    if since:
        filters.append(ChangeLog.ts >= since)
    if until:
        filters.append(ChangeLog.ts < until)

    total = db.scalar(select(func.count()).select_from(ChangeLog).where(*filters)) or 0
    # Ordered by id, not ts: ids are monotonic, and several changes in one
    # flush share a timestamp to the microsecond.
    rows = list(
        db.scalars(
            select(ChangeLog)
            .where(*filters)
            .order_by(ChangeLog.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return total, rows


def entity_history(db: Session, entity_type: str, entity_id: str) -> list[ChangeLog]:
    """Full timeline for one entity, oldest first."""
    return list(
        db.scalars(
            select(ChangeLog)
            .where(ChangeLog.entity_type == entity_type, ChangeLog.entity_id == entity_id)
            .order_by(ChangeLog.id)
        )
    )
