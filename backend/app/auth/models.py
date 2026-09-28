import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.db.types import UTCDateTime


#: Whoever set the restaurant up -- the first accounts, before one exists.
OWNER = "owner"
#: Granted by an owner: may edit the restaurant's details, like an owner.
ADMIN = "admin"
#: Everyone who signs up once the restaurant exists: sees it, can't edit it.
MEMBER = "member"
ROLES = (OWNER, ADMIN, MEMBER)
#: Who may change the restaurant profile and see who changed it.
MANAGER_ROLES = frozenset({OWNER, ADMIN})


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    # Stored lower-cased, so sign-in doesn't depend on how it was typed.
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    #: owner, admin or member; see ROLES. Accounts from before roles existed
    #: are owners, so upgrading takes no one's access away.
    role: Mapped[str] = mapped_column(String(20), default=OWNER, server_default=OWNER, nullable=False)
    restaurant_id: Mapped[str | None] = mapped_column(
        ForeignKey("restaurants.id", ondelete="SET NULL", name="fk_users_restaurant_id_restaurants"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    #: Set when an owner or admin turns the account off: it can't sign in,
    #: and every session it had was ended at that moment.
    disabled_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    # Two-step sign-in (app/auth/mfa.py). The secret is encrypted; backup
    # codes are hashed. `mfa_pending_secret` holds a secret being set up,
    # until a first code from the app proves it was scanned.
    mfa_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mfa_pending_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mfa_enabled_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    mfa_backup_codes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    #: The last code step accepted, so a code can't be used twice.
    mfa_last_step: Mapped[int | None] = mapped_column(Integer, nullable=True)

    @property
    def is_manager(self) -> bool:
        return self.role in MANAGER_ROLES

    @property
    def mfa_enabled(self) -> bool:
        return self.mfa_enabled_at is not None

    @property
    def mfa_setup_required(self) -> bool:
        """An owner or admin who must set up two-step sign-in before anything else."""
        from app.config import settings

        return self.is_manager and not self.mfa_enabled and settings.require_mfa and not settings.demo_mode


class AuthSession(Base):
    """One signed-in browser.

    The id is a SHA-256 of the cookie's token, never the token itself, so
    a leaked copy of this table can't be replayed as a login.
    """

    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class Invite(Base):
    """A one-time link that creates an account in a restaurant, with a role.

    Like sessions, only a SHA-256 of the link's token is stored.
    """

    __tablename__ = "invites"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    restaurant_id: Mapped[str] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE", name="fk_invites_restaurant_id_restaurants"),
        nullable=False, index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    #: When set, only this address can accept it.
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    accepted_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class PasswordReset(Base):
    """A one-time, short-lived link to set a new password, issued by a manager."""

    __tablename__ = "password_resets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    #: None when issued from the server's command line (scripts/reset_link.py).
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class SecurityEvent(Base):
    """Sign-ins, refusals and account changes, for owners and admins to review.

    Kept apart from the change log: it records attempts as well as changes,
    including ones that were stopped. Plain values, no foreign keys, so the
    record outlives the account it names.
    """

    __tablename__ = "security_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False, index=True)
    restaurant_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Who it happened to or was attempted as.
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    #: Who did it, when that's someone else (a manager turning an account off).
    actor_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class MfaChallenge(Base):
    """A sign-in waiting for its second step: the password was right, the
    code is still to come. Lives five minutes; its cookie holds the token."""

    __tablename__ = "mfa_challenges"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # SHA-256 of the token
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
