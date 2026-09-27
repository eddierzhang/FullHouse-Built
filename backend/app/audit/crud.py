from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.audit.models import ChangeLog


#: Changes to the restaurant profile: shown only to its owners and admins.
RESTAURANTS = "restaurants"


def visible_to(restaurant_id: str | None, is_manager: bool):
    """Filter hiding restaurant-profile changes a user may not see.

    Owners and admins see their own restaurant's; nobody sees another's,
    and members see none -- the history names who made each edit.
    """
    if is_manager and restaurant_id:
        return or_(ChangeLog.entity_type != RESTAURANTS, ChangeLog.entity_id == restaurant_id)
    return ChangeLog.entity_type != RESTAURANTS


def get_change(db: Session, change_id: int, visibility=None) -> ChangeLog | None:
    query = select(ChangeLog).where(ChangeLog.id == change_id)
    if visibility is not None:
        query = query.where(visibility)
    return db.scalars(query).first()


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
    visibility=None,
) -> tuple[int, list[ChangeLog]]:
    """Filtered page of changes, newest first, plus the unpaginated total."""
    filters = [] if visibility is None else [visibility]
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


def entity_history(db: Session, entity_type: str, entity_id: str, visibility=None) -> list[ChangeLog]:
    """Full timeline for one entity, oldest first."""
    query = select(ChangeLog).where(ChangeLog.entity_type == entity_type, ChangeLog.entity_id == entity_id)
    if visibility is not None:
        query = query.where(visibility)
    return list(db.scalars(query.order_by(ChangeLog.id)))
