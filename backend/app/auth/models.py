import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, String
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

    @property
    def is_manager(self) -> bool:
        return self.role in MANAGER_ROLES


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
