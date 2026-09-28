"""The security log: sign-ins, refusals and account changes.

Owners and admins read it on the Profile page. Each event is committed as
it's recorded, because most are recorded on the way to refusing a request
-- a rollback there must not take the record of the attempt with it.
"""

import logging

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth.models import SecurityEvent, User

logger = logging.getLogger("fullhouse.security")

#: What each event type means, for the log's readers.
TYPES = {
    "login_succeeded": "Signed in",
    "login_failed": "Wrong email or password",
    "login_blocked": "Sign-in blocked after repeated failures",
    "login_disabled": "Sign-in refused: access turned off",
    "new_device": "Signed in from a new device or network",
    "signup": "Created the restaurant's first account",
    "logout_everywhere": "Signed out everywhere",
    "invite_created": "Invite link created",
    "invite_revoked": "Invite link cancelled",
    "invite_accepted": "Joined with an invite",
    "role_changed": "Role changed",
    "access_disabled": "Access turned off",
    "access_enabled": "Access turned back on",
    "reset_link_issued": "Password reset link created",
    "password_reset": "Password reset with a link",
    "access_denied": "Tried something their role doesn't allow",
    "mfa_enabled": "Two-step sign-in turned on",
    "mfa_failed": "Wrong two-step code",
    "mfa_backup_code_used": "Signed in with a backup code",
    "mfa_reset": "Two-step sign-in reset",
}


def client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    # Behind Render or nginx every request arrives from the proxy, so the
    # first forwarded address is the one that identifies the visitor.
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else None)


def record(
    db: Session,
    type: str,
    request: Request | None = None,
    *,
    email: str | None = None,
    actor: User | None = None,
    restaurant_id: str | None = None,
    detail: dict | None = None,
) -> SecurityEvent:
    """Write one event and commit it."""
    assert type in TYPES, type
    agent = (request.headers.get("user-agent") or "")[:255] if request else ""
    event = SecurityEvent(
        type=type,
        restaurant_id=restaurant_id or (actor.restaurant_id if actor else None),
        email=email or (actor.email if actor else None),
        actor_email=actor.email if actor and email and email != actor.email else None,
        ip=client_ip(request),
        user_agent=agent or None,
        detail=detail,
    )
    db.add(event)
    db.commit()
    logger.info("security event %s email=%s ip=%s detail=%s", type, event.email, event.ip, detail)
    return event


def deny(db: Session, user: User, request: Request | None, message: str) -> HTTPException:
    """Record a refused attempt and return the 403 to raise."""
    record(db, "access_denied", request, actor=user,
           detail={"method": request.method, "path": request.url.path} if request else None)
    return HTTPException(status.HTTP_403_FORBIDDEN, message)


def is_new_device(db: Session, user: User, request: Request) -> bool:
    """Whether this network and browser pair has never signed in as `user` before."""
    ip, agent = client_ip(request), (request.headers.get("user-agent") or "")[:255] or None
    seen = (
        db.query(SecurityEvent.id)
        .filter(SecurityEvent.email == user.email, SecurityEvent.type == "login_succeeded",
                SecurityEvent.ip == ip, SecurityEvent.user_agent == agent)
        .first()
    )
    return seen is None
