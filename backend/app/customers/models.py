"""What customers tell the restaurant, and what the Assistant Manager makes of it.

A `CustomerRequest` is one thing a customer said -- a complaint, a
question, a compliment -- written down by whoever took it. Its `message`
is the original and is never edited: resolving a request adds a note
beside it. A `CustomerServiceReview` is the agent's summary of the open
requests from one run, and each `CustomerActionItem` is something that
review says the restaurant should do, pointing back at the requests it
came from.

None of these tables is in the change history (see audit/listener.py):
that history is open to the whole team, and these hold customers' names,
contact details and words, which are for owners and admins. Each row
records who logged, resolved or finished it instead.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.db.types import UTCDateTime

CHANNELS = ("in_person", "phone", "email", "online_review", "social_media", "other")
KINDS = ("complaint", "question", "request", "compliment", "other")
PRIORITIES = ("high", "normal", "low")
OPEN, RESOLVED, DONE = "open", "resolved", "done"


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CustomerRequest(Base):
    __tablename__ = "customer_requests"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str | None] = mapped_column(
        ForeignKey("restaurants.id", name="fk_customer_requests_restaurant_id_restaurants"), nullable=True, index=True
    )
    #: When the customer got in touch, which may be before it was written down here.
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(20), default="in_person", nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="other", nullable=False)
    customer_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    customer_contact: Mapped[str | None] = mapped_column(String(254), nullable=True)
    #: The customer's own words, as taken down. Never changed after it is saved.
    message: Mapped[str] = mapped_column(Text, nullable=False)
    #: Who wrote it down, by email -- as the change history names people.
    logged_by: Mapped[str] = mapped_column(String(254), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)

    status: Mapped[str] = mapped_column(String(16), default=OPEN, nullable=False, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(254), nullable=True)
    #: What was done about it, kept beside the original rather than over it.
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class CustomerServiceReview(Base):
    __tablename__ = "customer_service_reviews"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str | None] = mapped_column(
        ForeignKey("restaurants.id", name="fk_customer_service_reviews_restaurant_id_restaurants"),
        nullable=True, index=True,
    )
    #: The agent run that wrote it: one review per run.
    run_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("agent_runs.id"), nullable=True, unique=True)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    #: The requests the summary covers.
    request_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False, index=True)

    action_items: Mapped[list["CustomerActionItem"]] = relationship(
        back_populates="review", order_by="CustomerActionItem.created_at", cascade="all, delete-orphan"
    )


class CustomerActionItem(Base):
    __tablename__ = "customer_action_items"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str | None] = mapped_column(
        ForeignKey("restaurants.id", name="fk_customer_action_items_restaurant_id_restaurants"),
        nullable=True, index=True,
    )
    review_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("customer_service_reviews.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[str] = mapped_column(String(8), default="normal", nullable=False)
    #: The requests this comes from, so anyone can read the originals behind it.
    request_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)

    status: Mapped[str] = mapped_column(String(16), default=OPEN, nullable=False)
    done_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    done_by: Mapped[str | None] = mapped_column(String(254), nullable=True)

    review: Mapped[CustomerServiceReview] = relationship(back_populates="action_items")
