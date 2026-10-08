import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.auth import schemas
from app.audit.context import HUMAN, Actor, actor_context
from app.auth import events, mfa
from app.auth.deps import SESSION_COOKIE, current_user
from app.auth.models import ADMIN, MEMBER, OWNER, AuthSession, Invite, MfaChallenge, PasswordReset, SecurityEvent, User
from app.auth.security import DUMMY_HASH, hash_password, new_session_token, token_id, verify_password
from app.config import settings
from app.db.session import get_db

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

SESSION_DAYS = 14
INVITE_DAYS = 7
RESET_MINUTES = 60
MFA_CHALLENGE_MINUTES = 5
MFA_MAX_ATTEMPTS = 5
#: Holds the sign-in waiting for its code; sent only to the auth routes.
MFA_COOKIE = "fullhouse_mfa"


class _FailedLogins:
    """Failed sign-ins per client address, in a sliding window.

    In memory, which is enough for the single process this app runs as:
    it turns online password guessing from thousands a minute into a
    handful per quarter hour.
    """

    LIMIT = 10
    WINDOW_SECONDS = 15 * 60

    def __init__(self):
        self._lock = threading.Lock()
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _trim(self, key: str, now: float) -> deque[float]:
        failures = self._failures[key]
        while failures and failures[0] <= now - self.WINDOW_SECONDS:
            failures.popleft()
        return failures

    def blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._trim(key, time.monotonic())) >= self.LIMIT

    def record(self, key: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._trim(key, now).append(now)

    def clear(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._failures.clear()
            else:
                self._failures.pop(key, None)


#: Per client address: stops one visitor guessing many passwords.
failed_logins = _FailedLogins()
#: Per account: stops many addresses guessing one account's password.
failed_accounts = _FailedLogins()


def _client_key(request: Request) -> str:
    return events.client_ip(request) or "unknown"


def _is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").startswith("https")


def _start_session(db: Session, user: User, request: Request, response: Response) -> None:
    token = new_session_token()
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    db.add(AuthSession(id=token_id(token), user_id=user.id, expires_at=expires))
    db.commit()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_DAYS * 24 * 3600,
        httponly=True,  # unreadable from page scripts
        samesite="lax",  # not sent on other sites' form posts
        secure=_is_https(request),
        path="/",
    )


def _signup_open(db: Session) -> bool:
    """Whether a new restaurant can sign up. ALLOW_SIGNUP opens the site to
    any number of them; without it there is one, whose owner is whoever signs
    up first. The demo takes none: its data is replaced every day."""
    from app.restaurant.models import Restaurant

    if db.query(Restaurant.id).first() is None:
        return True
    return settings.allow_signup and not settings.demo_mode


@router.get("/options", response_model=schemas.AuthOptions)
def options(db: Session = Depends(get_db)):
    """What the login page should offer. Public: it is read before signing in."""
    demo = None
    if settings.demo_mode:
        from app.demo import DEMO_EMAIL, DEMO_PASSWORD

        demo = schemas.DemoCredentials(email=DEMO_EMAIL, password=DEMO_PASSWORD)
    return schemas.AuthOptions(signup_enabled=_signup_open(db), demo=demo)


@router.post("/signup", response_model=schemas.UserOut, status_code=201)
def signup(body: schemas.SignupIn, request: Request, response: Response, db: Session = Depends(get_db)):
    """Open a new restaurant: its owner's account, and the restaurant itself, ready for setup."""
    from app.restaurant.profile import create_restaurant

    if not _signup_open(db):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This site isn't taking new restaurants. To join one that's already here, ask its owner or a manager "
            "for an invite link.",
        )
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with that email already exists. Sign in instead.")
    # Signing up never joins a restaurant that's already here -- that takes an
    # invite from its owner. It starts a new one, which nobody else can see.
    user = User(email=body.email, name=body.name, password_hash=hash_password(body.password), role=OWNER)
    # Under the new owner's name, so the restaurant's history begins with them.
    with actor_context(Actor(type=HUMAN, id=body.email[:64])):
        create_restaurant(db, user, body.restaurant_name)
        db.add(user)
        db.flush()
        _start_session(db, user, request, response)
    events.record(db, "signup", request, actor=user)
    return user


@router.post("/login", response_model=schemas.UserOut | schemas.MfaRequired)
def login(body: schemas.LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    key = _client_key(request)
    if failed_logins.blocked(key) or failed_accounts.blocked(body.email):
        events.record(db, "login_blocked", request, email=body.email)
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed attempts. Try again in 15 minutes.")

    user = db.query(User).filter(User.email == body.email).first()
    if not verify_password(body.password, user.password_hash if user else DUMMY_HASH) or user is None:
        failed_logins.record(key)
        failed_accounts.record(body.email)
        events.record(db, "login_failed", request, email=body.email,
                      restaurant_id=user.restaurant_id if user else None)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That email and password don't match.")
    if user.disabled_at:
        # Only after the password matched, so this can't be used to probe accounts.
        events.record(db, "login_disabled", request, actor=user)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your access has been turned off. Ask the owner or a manager.")

    failed_logins.clear(key)
    if user.mfa_enabled:
        # The password was right; the session waits for the code.
        token = new_session_token()
        db.add(MfaChallenge(id=token_id(token), user_id=user.id,
                            expires_at=datetime.now(timezone.utc) + timedelta(minutes=MFA_CHALLENGE_MINUTES)))
        db.commit()
        response.set_cookie(MFA_COOKIE, token, max_age=MFA_CHALLENGE_MINUTES * 60, httponly=True, samesite="lax",
                            secure=_is_https(request), path="/api/v1/auth")
        return schemas.MfaRequired()
    failed_accounts.clear(body.email)
    _complete_sign_in(db, user, request, response)
    return user


def _complete_sign_in(db: Session, user: User, request: Request, response: Response) -> None:
    if events.is_new_device(db, user, request) and db.query(SecurityEvent.id).filter(
        SecurityEvent.email == user.email, SecurityEvent.type == "login_succeeded"
    ).first():
        events.record(db, "new_device", request, actor=user)
    _start_session(db, user, request, response)
    events.record(db, "login_succeeded", request, actor=user)


def _second_step(db: Session, user: User, code: str) -> str | None:
    """Check a code from the app, or a backup code, and spend it. Returns which it was."""
    secret = mfa.decrypt(user.mfa_secret) if user.mfa_secret else None
    step = mfa.matching_step(secret, code) if secret else None
    # A code works once: one already accepted for this or a later step is refused.
    if step is not None and (user.mfa_last_step is None or step > user.mfa_last_step):
        user.mfa_last_step = step
        db.commit()
        return "app"
    remaining = mfa.use_backup_code(user.mfa_backup_codes or [], code)
    if remaining is not None:
        user.mfa_backup_codes = remaining
        db.commit()
        return "backup"
    return None


@router.post("/login/mfa", response_model=schemas.UserOut)
def login_second_step(body: schemas.CodeIn, request: Request, response: Response, db: Session = Depends(get_db)):
    """The code that finishes a sign-in the password started."""
    token = request.cookies.get(MFA_COOKIE)
    challenge = db.get(MfaChallenge, token_id(token)) if token else None
    if challenge is None or challenge.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That took too long. Sign in again.")
    user = db.get(User, challenge.user_id)
    if failed_accounts.blocked(user.email):
        events.record(db, "login_blocked", request, actor=user)
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed attempts. Try again in 15 minutes.")

    used = _second_step(db, user, body.code)
    if used is None:
        challenge.attempts += 1
        failed_accounts.record(user.email)
        if challenge.attempts >= MFA_MAX_ATTEMPTS:
            db.delete(challenge)
        db.commit()
        events.record(db, "mfa_failed", request, actor=user)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That code didn't work." if challenge.attempts < MFA_MAX_ATTEMPTS
                            else "Too many wrong codes. Sign in again.")

    db.delete(challenge)
    db.commit()
    failed_accounts.clear(user.email)
    if used == "backup":
        events.record(db, "mfa_backup_code_used", request, actor=user,
                      detail={"remaining": len(user.mfa_backup_codes or [])})
    response.delete_cookie(MFA_COOKIE, path="/api/v1/auth", httponly=True, samesite="lax", secure=_is_https(request))
    _complete_sign_in(db, user, request, response)
    return user


# --- two-step sign-in, for your own account ---------------------------------------------


def _require_code(db: Session, user: User, code: str | None, request: Request) -> None:
    """Changing two-step settings once they're on takes a current code."""
    if user.mfa_enabled and (not code or _second_step(db, user, code) is None):
        events.record(db, "mfa_failed", request, actor=user)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Enter a current code from your authenticator app.")


@router.post("/mfa/setup", response_model=schemas.MfaSetupOut)
def mfa_setup(body: schemas.OptionalCodeIn, request: Request, user: User = Depends(current_user),
              db: Session = Depends(get_db)):
    """A new secret to scan. Two-step stays as it was until /mfa/enable proves the scan."""
    _require_code(db, user, body.code, request)
    secret = mfa.new_secret()
    user.mfa_pending_secret = mfa.encrypt(secret)
    db.commit()
    uri = mfa.provisioning_uri(secret, user.email)
    return schemas.MfaSetupOut(secret=secret, otpauth_uri=uri, qr_svg=mfa.qr_svg(uri))


@router.post("/mfa/enable", response_model=schemas.BackupCodesOut)
def mfa_enable(body: schemas.CodeIn, request: Request, user: User = Depends(current_user),
               db: Session = Depends(get_db)):
    """Turn two-step on with the secret just scanned, and hand out backup codes -- once."""
    pending = mfa.decrypt(user.mfa_pending_secret) if user.mfa_pending_secret else None
    step = mfa.matching_step(pending, body.code) if pending else None
    if step is None:
        raise HTTPException(422, "That code didn't match. Check the app and try the newest one.")
    codes, hashes = mfa.new_backup_codes()
    user.mfa_secret, user.mfa_pending_secret = user.mfa_pending_secret, None
    user.mfa_enabled_at = datetime.now(timezone.utc)
    user.mfa_last_step = step
    user.mfa_backup_codes = hashes
    db.commit()
    events.record(db, "mfa_enabled", request, actor=user)
    return schemas.BackupCodesOut(codes=codes)


@router.post("/mfa/backup-codes", response_model=schemas.BackupCodesOut)
def mfa_new_backup_codes(body: schemas.CodeIn, request: Request, user: User = Depends(current_user),
                         db: Session = Depends(get_db)):
    """A fresh set of backup codes; the old ones stop working."""
    if not user.mfa_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Turn two-step sign-in on first.")
    _require_code(db, user, body.code, request)
    codes, hashes = mfa.new_backup_codes()
    user.mfa_backup_codes = hashes
    db.commit()
    return schemas.BackupCodesOut(codes=codes)


def clear_mfa(user: User) -> None:
    user.mfa_secret = user.mfa_pending_secret = None
    user.mfa_enabled_at = None
    user.mfa_backup_codes = None
    user.mfa_last_step = None


@router.post("/mfa/disable", response_model=schemas.UserOut)
def mfa_disable(body: schemas.CodeIn, request: Request, user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    """Turn two-step off -- only where it isn't required (members)."""
    if user.is_manager and settings.require_mfa and not settings.demo_mode:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Owners and admins must keep two-step sign-in on.")
    _require_code(db, user, body.code, request)
    clear_mfa(user)
    db.commit()
    events.record(db, "mfa_reset", request, actor=user)
    db.refresh(user)
    return user


@router.post("/logout", status_code=204)
def logout(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        session = db.get(AuthSession, token_id(token))
        if session is not None:
            db.delete(session)
            db.commit()
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="lax", secure=_is_https(request))
    return response


@router.get("/me", response_model=schemas.UserOut)
def me(user: User = Depends(current_user)):
    return user


# --- your sessions ----------------------------------------------------------------


@router.post("/logout-everywhere", status_code=204)
def logout_everywhere(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """End every session this account has, this browser's included."""
    db.query(AuthSession).filter(AuthSession.user_id == user.id).delete()
    db.commit()
    events.record(db, "logout_everywhere", request, actor=user)
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="lax", secure=_is_https(request))
    return response


# --- team -------------------------------------------------------------------------


def _teammates(db: Session, user: User):
    """Accounts in the caller's restaurant. One with no restaurant has no team but itself."""
    if user.restaurant_id:
        return db.query(User).filter(User.restaurant_id == user.restaurant_id)
    return db.query(User).filter(User.id == user.id)


def _manager(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
    if not user.is_manager:
        raise events.deny(db, user, request, "Only the owner or an admin can manage the team.")
    return user


def _can_manage(actor: User, target: User) -> bool:
    """Owners manage admins and members; admins manage members. Nobody manages
    an owner, or themselves, from here."""
    if target.id == actor.id or target.role == OWNER:
        return False
    return actor.role == OWNER or (actor.role == ADMIN and target.role == MEMBER)


def _teammate(db: Session, actor: User, user_id: str, request: Request) -> User:
    """Someone in the caller's restaurant whom the caller may manage."""
    # Looked up within the caller's restaurant, so another's accounts read as missing.
    target = _teammates(db, actor).filter(User.id == user_id).first()
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No one with that account works here.")
    if not _can_manage(actor, target):
        raise events.deny(db, actor, request, "You can't change that account.")
    return target


@router.get("/team", response_model=list[schemas.UserOut])
def team(user: User = Depends(_manager), db: Session = Depends(get_db)):
    """Who can sign in to this restaurant, and with what role. Owners and admins only."""
    return _teammates(db, user).order_by(User.created_at, User.email).all()


@router.patch("/team/{user_id}", response_model=schemas.UserOut)
def set_role(
    user_id: str, body: schemas.RoleIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """Make someone an admin, or take it away. Owners only, and never an owner's own role."""
    if user.role != OWNER:
        raise events.deny(db, user, request, "Only the owner can change who is an admin.")
    target = _teammates(db, user).filter(User.id == user_id).first()
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No one with that account works here.")
    if target.role == OWNER:
        raise HTTPException(status.HTTP_409_CONFLICT, "An owner's role can't be changed here.")
    before = target.role
    target.role = body.role
    db.commit()
    db.refresh(target)
    if before != target.role:
        events.record(db, "role_changed", request, email=target.email, actor=user,
                      detail={"from": before, "to": target.role})
    return target


@router.post("/team/{user_id}/disable", response_model=schemas.UserOut)
def disable(user_id: str, request: Request, user: User = Depends(_manager), db: Session = Depends(get_db)):
    """Turn someone's access off, at once: every session they have ends now."""
    target = _teammate(db, user, user_id, request)
    if target.disabled_at is None:
        target.disabled_at = datetime.now(timezone.utc)
        db.query(AuthSession).filter(AuthSession.user_id == target.id).delete()
        db.commit()
        events.record(db, "access_disabled", request, email=target.email, actor=user)
    db.refresh(target)
    return target


@router.post("/team/{user_id}/enable", response_model=schemas.UserOut)
def enable(user_id: str, request: Request, user: User = Depends(_manager), db: Session = Depends(get_db)):
    target = _teammate(db, user, user_id, request)
    if target.disabled_at is not None:
        target.disabled_at = None
        db.commit()
        events.record(db, "access_enabled", request, email=target.email, actor=user)
    db.refresh(target)
    return target


@router.post("/team/{user_id}/reset-mfa", response_model=schemas.UserOut)
def reset_mfa(user_id: str, request: Request, user: User = Depends(_manager), db: Session = Depends(get_db)):
    """For a lost phone: clear their two-step sign-in and end their sessions.
    They set it up again when they next sign in."""
    target = _teammate(db, user, user_id, request)
    clear_mfa(target)
    db.query(AuthSession).filter(AuthSession.user_id == target.id).delete()
    db.commit()
    events.record(db, "mfa_reset", request, email=target.email, actor=user)
    db.refresh(target)
    return target


# --- password resets -----------------------------------------------------------------


def issue_reset(db: Session, target: User, issued_by: User | None) -> tuple[str, PasswordReset]:
    """A one-time link for `target` to set a new password; earlier unused ones stop working."""
    now = datetime.now(timezone.utc)
    db.query(PasswordReset).filter(PasswordReset.user_id == target.id, PasswordReset.used_at.is_(None)).update(
        {PasswordReset.used_at: now}
    )
    token = new_session_token()
    reset = PasswordReset(token_hash=token_id(token), user_id=target.id, created_by=issued_by.id if issued_by else None,
                          expires_at=now + timedelta(minutes=RESET_MINUTES))
    db.add(reset)
    db.commit()
    return token, reset


@router.post("/team/{user_id}/reset-link", response_model=schemas.ResetLink)
def reset_link(user_id: str, request: Request, user: User = Depends(_manager), db: Session = Depends(get_db)):
    """A link, valid for an hour, that lets a teammate set a new password. Hand it to them yourself."""
    target = _teammate(db, user, user_id, request)
    token, reset = issue_reset(db, target, user)
    events.record(db, "reset_link_issued", request, email=target.email, actor=user)
    return schemas.ResetLink(token=token, expires_at=reset.expires_at)


def _live_reset(db: Session, token: str) -> PasswordReset:
    reset = db.query(PasswordReset).filter(PasswordReset.token_hash == token_id(token)).first()
    if reset is None or reset.used_at is not None or reset.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This link has expired or was already used. Ask for a new one.")
    return reset


@router.get("/reset/{token}", response_model=schemas.ResetPreview)
def preview_reset(token: str, db: Session = Depends(get_db)):
    """Whose password a reset link is for. Public: it's opened before signing in."""
    user = db.get(User, _live_reset(db, token).user_id)
    return schemas.ResetPreview(name=user.name, email=user.email)


@router.post("/reset/{token}", response_model=schemas.UserOut)
def use_reset(token: str, body: schemas.NewPasswordIn, request: Request, response: Response,
              db: Session = Depends(get_db)):
    """Set the new password. Every other session the account had ends."""
    reset = _live_reset(db, token)
    user = db.get(User, reset.user_id)
    if user.disabled_at:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your access has been turned off. Ask the owner or a manager.")
    user.password_hash = hash_password(body.password)
    reset.used_at = datetime.now(timezone.utc)
    db.query(AuthSession).filter(AuthSession.user_id == user.id).delete()
    db.commit()
    events.record(db, "password_reset", request, actor=user)
    _start_session(db, user, request, response)
    return user


# --- invites -------------------------------------------------------------------------


@router.post("/invites", response_model=schemas.InviteCreated, status_code=201)
def create_invite(body: schemas.InviteIn, request: Request, user: User = Depends(_manager),
                  db: Session = Depends(get_db)):
    """A one-time link, valid for a week, that creates an account in this restaurant."""
    if body.role == ADMIN and user.role != OWNER:
        raise events.deny(db, user, request, "Only the owner can invite admins.")
    if not user.restaurant_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Set the restaurant up before inviting anyone.")
    if body.email and db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status.HTTP_409_CONFLICT, f"{body.email} already has an account.")
    token = new_session_token()
    invite = Invite(token_hash=token_id(token), restaurant_id=user.restaurant_id, role=body.role, email=body.email,
                    created_by=user.id, expires_at=datetime.now(timezone.utc) + timedelta(days=INVITE_DAYS))
    db.add(invite)
    db.commit()
    db.refresh(invite)
    events.record(db, "invite_created", request, email=body.email, actor=user, detail={"role": body.role})
    return schemas.InviteCreated(**schemas.InviteOut.model_validate(invite).model_dump(), token=token)


@router.get("/invites", response_model=list[schemas.InviteOut])
def list_invites(user: User = Depends(_manager), db: Session = Depends(get_db)):
    """Invites not yet used, cancelled or expired."""
    return (
        db.query(Invite)
        .filter(Invite.restaurant_id == user.restaurant_id, Invite.accepted_at.is_(None), Invite.revoked_at.is_(None),
                Invite.expires_at > datetime.now(timezone.utc))
        .order_by(Invite.created_at.desc())
        .all()
    )


@router.delete("/invites/{invite_id}", status_code=204)
def revoke_invite(invite_id: str, request: Request, user: User = Depends(_manager), db: Session = Depends(get_db)):
    invite = db.query(Invite).filter(Invite.id == invite_id, Invite.restaurant_id == user.restaurant_id).first()
    if invite is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such invite.")
    if invite.revoked_at is None and invite.accepted_at is None:
        invite.revoked_at = datetime.now(timezone.utc)
        db.commit()
        events.record(db, "invite_revoked", request, email=invite.email, actor=user)
    return Response(status_code=204)


def _live_invite(db: Session, token: str) -> Invite:
    invite = db.query(Invite).filter(Invite.token_hash == token_id(token)).first()
    if (invite is None or invite.accepted_at is not None or invite.revoked_at is not None
            or invite.expires_at <= datetime.now(timezone.utc)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This invite has expired or was already used. Ask for a new one.")
    return invite


@router.get("/invite/{token}", response_model=schemas.InvitePreview)
def preview_invite(token: str, db: Session = Depends(get_db)):
    """Which restaurant an invite is for. Public: it's opened before signing in."""
    from app.restaurant.models import Restaurant

    invite = _live_invite(db, token)
    restaurant = db.get(Restaurant, invite.restaurant_id)
    return schemas.InvitePreview(restaurant_name=restaurant.name if restaurant else None, role=invite.role,
                                 email=invite.email)


@router.post("/invite/{token}", response_model=schemas.UserOut, status_code=201)
def accept_invite(token: str, body: schemas.AcceptInviteIn, request: Request, response: Response,
                  db: Session = Depends(get_db)):
    """Create the account the invite is for, and sign it in."""
    invite = _live_invite(db, token)
    if invite.email and body.email != invite.email:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"This invite is for {invite.email}.")
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with that email already exists. Sign in instead.")
    user = User(email=body.email, name=body.name, password_hash=hash_password(body.password), role=invite.role,
                restaurant_id=invite.restaurant_id)
    db.add(user)
    db.flush()
    invite.accepted_at = datetime.now(timezone.utc)
    invite.accepted_by = user.id
    _start_session(db, user, request, response)
    events.record(db, "invite_accepted", request, actor=user, detail={"role": invite.role})
    return user


# --- the security log ------------------------------------------------------------------


@router.get("/security-events", response_model=list[schemas.SecurityEventOut])
def security_events(limit: int = 200, user: User = Depends(_manager), db: Session = Depends(get_db)):
    """This restaurant's sign-ins, refusals and account changes, newest first."""
    rows = (
        db.query(SecurityEvent)
        .filter(SecurityEvent.restaurant_id == user.restaurant_id)
        .order_by(SecurityEvent.ts.desc(), SecurityEvent.id.desc())
        .limit(min(max(limit, 1), 500))
        .all()
    )
    return [schemas.SecurityEventOut(**{c: getattr(r, c) for c in (
        "id", "ts", "type", "email", "actor_email", "ip", "user_agent", "detail")}, label=events.TYPES[r.type])
        for r in rows]
