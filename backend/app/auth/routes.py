import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.auth import schemas
from app.auth.deps import SESSION_COOKIE, current_user
from app.auth.models import MEMBER, OWNER, AuthSession, User
from app.auth.security import DUMMY_HASH, hash_password, new_session_token, token_id, verify_password
from app.config import settings
from app.db.session import get_db

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

SESSION_DAYS = 14


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


failed_logins = _FailedLogins()


def _client_key(request: Request) -> str:
    # Behind Render or nginx every request arrives from the proxy, so the
    # first forwarded address is the one that identifies the visitor.
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


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


@router.get("/options", response_model=schemas.AuthOptions)
def options():
    """What the login page should offer. Public: it is read before signing in."""
    demo = None
    if settings.demo_mode:
        from app.demo import DEMO_EMAIL, DEMO_PASSWORD

        demo = schemas.DemoCredentials(email=DEMO_EMAIL, password=DEMO_PASSWORD)
    return schemas.AuthOptions(signup_enabled=settings.allow_signup, demo=demo)


@router.post("/signup", response_model=schemas.UserOut, status_code=201)
def signup(body: schemas.SignupIn, request: Request, response: Response, db: Session = Depends(get_db)):
    if not settings.allow_signup:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "New accounts are turned off here.")
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with that email already exists. Sign in instead.")
    # One restaurant per site. Before it exists, whoever signs up is an
    # owner and can set it up; after, a new account joins it as a member,
    # who can't change its details until an owner makes them an admin.
    from app.restaurant.profile import get_restaurant

    restaurant = get_restaurant(db)
    user = User(email=body.email, name=body.name, password_hash=hash_password(body.password),
                role=MEMBER if restaurant else OWNER, restaurant_id=restaurant.id if restaurant else None)
    db.add(user)
    db.flush()
    _start_session(db, user, request, response)
    return user


@router.post("/login", response_model=schemas.UserOut)
def login(body: schemas.LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    key = _client_key(request)
    if failed_logins.blocked(key):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed attempts. Try again in 15 minutes.")

    user = db.query(User).filter(User.email == body.email).first()
    if not verify_password(body.password, user.password_hash if user else DUMMY_HASH) or user is None:
        failed_logins.record(key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That email and password don't match.")

    failed_logins.clear(key)
    _start_session(db, user, request, response)
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


# --- team -------------------------------------------------------------------------


def _teammates(db: Session, user: User):
    """Accounts in the caller's restaurant. Before it exists, every account --
    they are all owners setting up the one restaurant this site will have."""
    query = db.query(User)
    if user.restaurant_id:
        query = query.filter(User.restaurant_id == user.restaurant_id)
    return query


@router.get("/team", response_model=list[schemas.UserOut])
def team(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Who can sign in to this restaurant, and with what role. Owners and admins only."""
    if not user.is_manager:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner or an admin can see the team.")
    return _teammates(db, user).order_by(User.created_at, User.email).all()


@router.patch("/team/{user_id}", response_model=schemas.UserOut)
def set_role(user_id: str, body: schemas.RoleIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Make someone an admin, or take it away. Owners only, and never an owner's own role."""
    if user.role != OWNER:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can change who is an admin.")
    # Looked up within the caller's restaurant, so another's accounts read as missing.
    target = _teammates(db, user).filter(User.id == user_id).first()
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No one with that account works here.")
    if target.role == OWNER:
        raise HTTPException(status.HTTP_409_CONFLICT, "An owner's role can't be changed here.")
    target.role = body.role
    db.commit()
    db.refresh(target)
    return target
