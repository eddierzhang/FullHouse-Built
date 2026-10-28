"""Platform tables: per-restaurant feature flags, the append-only event log, the job queue and the
raw webhook inbox. None of these is a thing a person edits; they are how the parts talk."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.db.types import UTCDateTime


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RestaurantFlag(Base):
    """A feature switched on for one restaurant. Phases ship dark and are turned on per customer."""

    __tablename__ = "restaurant_flags"
    __table_args__ = (UniqueConstraint("restaurant_id", "flag", name="uq_restaurant_flags_restaurant_flag"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(
        ForeignKey("restaurants.id", name="fk_restaurant_flags_restaurant_id_restaurants"), nullable=False, index=True
    )
    flag: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now, nullable=False)


class Event(Base):
    """One thing that happened, for the machines: `invoice.ingested`, `proposal.approved`. Append-only.

    `change_log` is the record people read; this is the one stages subscribe to, so adding a
    forecast means subscribing to `sales_day.synced`, not editing the sync.
    """

    __tablename__ = "events"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    restaurant_id: Mapped[str | None] = mapped_column(
        ForeignKey("restaurants.id", name="fk_events_restaurant_id_restaurants"), nullable=True, index=True
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    aggregate_table: Mapped[str | None] = mapped_column(String(64), nullable=True)
    aggregate_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False, index=True)


QUEUED, ACTIVE, DONE, FAILED = "queued", "active", "done", "failed"


class Job(Base):
    """A unit of work for a worker: idempotent, keyed, leased, retried with backoff.

    The web process only enqueues; a worker (a thread in the same process by default, or
    `python -m app.worker`) claims and runs it. `singleton_key` is deterministic from
    (restaurant, kind, external id or week), so a replayed webhook or a double click collapses
    into one job while one is queued or active.
    """

    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("singleton_key", "status_group", name="uq_jobs_singleton_open"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str | None] = mapped_column(
        ForeignKey("restaurants.id", name="fk_jobs_restaurant_id_restaurants"), nullable=True, index=True
    )
    queue: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    singleton_key: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    #: "open" while queued or active, else the job's own id: lets the unique constraint collapse
    #: duplicates while one is in flight and still keep every finished job's row.
    status_group: Mapped[str] = mapped_column(String(32), default="open", nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(10), default=QUEUED, nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    run_after: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False, index=True)
    leased_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    worker: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Workflow(Base):
    """A scheduled agent run for one restaurant: what, when (cron, on the restaurant's clock), and whether it is on.

    Default rows are made when setup finishes: digest and calendar_scan on; the rest off until the phase that
    ships them is switched on by flag.
    """

    __tablename__ = "workflows"
    __table_args__ = (UniqueConstraint("restaurant_id", "kind", name="uq_workflows_restaurant_kind"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(
        ForeignKey("restaurants.id", name="fk_workflows_restaurant_id_restaurants"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)  # digest|po_draft|prep_sheet|recipe_onboarding|calendar_scan|forecast|pnl
    cron: Mapped[str] = mapped_column(String(40), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    approval_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now, nullable=False)


class WebhookInbox(Base):
    """Every webhook as it arrived, once. The handler writes here and enqueues; it never writes typed
    tables, so a flood of webhooks cannot bypass idempotency."""

    __tablename__ = "webhook_inbox"
    __table_args__ = (UniqueConstraint("provider", "external_id", name="uq_webhook_inbox_provider_external"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    restaurant_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False, index=True)
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
