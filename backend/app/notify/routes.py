"""What the restaurant has been told: the bell in the app. Reading them, and marking them read."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.auth.models import MANAGER_ROLES, User
from app.db.session import get_db
from app.notify import service
from app.restaurant.inventory import scope_for

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


class ReadIn(BaseModel):
    #: The ones to mark read; empty or missing means everything the person can see.
    ids: list[str] = Field(default_factory=list, max_length=200)


def _out(db: Session, user: User) -> dict:
    restaurant_id = scope_for(db, user)
    if restaurant_id is None:
        return {"unread": 0, "items": []}
    rows = service.visible_to(db, restaurant_id, user.email, user.role in MANAGER_ROLES)
    return {"unread": sum(1 for r in rows if r.read_at is None),
            "items": [{"id": r.id, "kind": r.kind, "title": r.title, "body": r.body, "link": r.link, "created_at": r.created_at,
                       "read": r.read_at is not None} for r in rows]}


@router.get("")
def list_notifications(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """The last thirty things worth knowing, newest first, and how many are unread."""
    return _out(db, user)


@router.post("/read")
def mark_read(body: ReadIn | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Mark notifications read: the ones named, or all of them. Only ones this person can see."""
    restaurant_id = scope_for(db, user)
    if restaurant_id is not None:
        wanted = set(body.ids) if body and body.ids else None
        for row in service.visible_to(db, restaurant_id, user.email, user.role in MANAGER_ROLES, limit=200):
            if row.read_at is None and (wanted is None or row.id in wanted):
                row.read_at = datetime.now(timezone.utc)
        db.commit()
    return _out(db, user)
