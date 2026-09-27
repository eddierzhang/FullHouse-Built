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


def _user_for_token(db: Session, token: str) -> User | None:
    session = db.get(AuthSession, token_id(token))
    if session is None:
        return None
    if session.expires_at <= datetime.now(timezone.utc):
        db.delete(session)
        db.commit()
        return None
    return db.get(User, session.user_id)


async def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    user = await run_in_threadpool(_user_for_token, db, token) if token else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")
    # Change history records who did it by email: unique, and readable.
    set_actor(Actor(type=HUMAN, id=user.email[:64]))
    return user
