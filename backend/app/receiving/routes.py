"""Checking deliveries in, and reading suppliers' replies to orders."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import current_user, manager
from app.auth.models import User
from app.db.session import get_db
from app.platform import flags
from app.restaurant.inventory import scope_for

router = APIRouter(prefix="/api/v1/receiving", tags=["receiving"])


def _scope(db: Session, user: User) -> str:
    """The signed-in user's restaurant, with this feature switched on for it."""
    restaurant_id = scope_for(db, user)
    if restaurant_id is None:
        raise HTTPException(404, "Set up your restaurant first.")
    if not flags.enabled(db, restaurant_id, flags.V3_RECEIVING):
        raise HTTPException(404, "Receiving isn't switched on for this restaurant (Profile, Features).")
    return restaurant_id
