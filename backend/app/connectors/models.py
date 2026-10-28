"""The providers the site knows (global) and the systems each restaurant has connected."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.db.types import UTCDateTime


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


KINDS = ("pos", "accounting", "payroll", "ap", "email")
CAPABILITIES = ("sales_days", "sales_items", "labor_days", "invoices", "push_invoices", "menu", "documents")
AUTH_KINDS = ("oauth", "api_key", "none", "partner")


class ConnectorProvider(Base):
    """A provider the app can connect to: not tenant-scoped on purpose. Adding one is a row
    plus an adapter, not a UI change."""

    __tablename__ = "connector_providers"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    auth: Mapped[str] = mapped_column(String(16), nullable=False)
    capabilities: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: What to tell the person: where to get a key, what the CSV should look like.
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class Connection(Base):
    """One connected external system for one restaurant."""

    __tablename__ = "connections"
    __table_args__ = (UniqueConstraint("restaurant_id", "provider", name="uq_connections_restaurant_provider"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(
        ForeignKey("restaurants.id", name="fk_connections_restaurant_id_restaurants"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), ForeignKey("connector_providers.provider"), nullable=False)
    #: Encrypted at rest (app.connectors.crypto); decrypted only in the worker.
    credentials_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    rate_state: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="active", nullable=False)  # active|error|revoked
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Provider-specific, never secret: the inbound address, the CSV profile, the merchant id.
    settings: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now, nullable=False)
