"""The schedule's part of the Labor week page. Phase 6 fills this in; until then the week has no schedule."""

from __future__ import annotations

from sqlalchemy.orm import Session


def attach(db: Session, restaurant_id: str, week: dict, user) -> dict:
    week["schedule"] = None
    return week
