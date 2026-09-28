"""The dependency that guards every data route.

`current_user` is async on purpose. FastAPI resolves async dependencies in
the request's own task, and runs the (sync) route handler in a copy of
that task's context -- so the actor set here is the one the audit listener
sees for every change the handler makes. A sync dependency would run in a
throwaway thread context and the attribution would be lost.
"""

from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.audit.context import HUMAN, Actor, set_actor
from app.auth.models import AuthSession, User
from app.auth.security import token_id
from app.db.session import get_db

SESSION_COOKIE = "fullhouse_session"

#: All an owner or admin may do before setting up two-step sign-in.
MFA_SETUP_PATHS = frozenset({
    "/api/v1/auth/me", "/api/v1/auth/logout", "/api/v1/auth/logout-everywhere",
    "/api/v1/auth/mfa/setup", "/api/v1/auth/mfa/enable",
})


def _user_for_token(db: Session, token: str) -> User | None:
    session = db.get(AuthSession, token_id(token))
    if session is None:
        return None
    if session.expires_at <= datetime.now(timezone.utc):
        db.delete(session)
        db.commit()
        return None
    user = db.get(User, session.user_id)
    # Turning access off ends the sessions too; this catches any still in flight.
    return None if user is None or user.disabled_at else user


async def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    user = await run_in_threadpool(_user_for_token, db, token) if token else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")
    if user.mfa_setup_required and request.url.path not in MFA_SETUP_PATHS:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Set up two-step sign-in first: owners and admins need it.")
    # Change history records who did it by email: unique, and readable --
    # and for which restaurant, so nobody else's history ever shows it.
    set_actor(Actor(type=HUMAN, id=user.email[:64], restaurant_id=user.restaurant_id))
    return user


def manager(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
    """Owners and admins only: editing the menu and suppliers, running and
    approving the agent, undoing changes, and the profit figures. A refusal
    goes in the security log."""
    if not user.is_manager:
        from app.auth.events import deny

        raise deny(db, user, request, "Only the owner or an admin can do that.")
    return user
