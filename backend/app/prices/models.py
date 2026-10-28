"""Price intelligence: supplier invoices, the items they resolve to, and what the agent makes of them.

An `Invoice` is one supplier bill, however it arrived. Each `InvoiceLine`
is kept as printed; matching sets `item_id` to a canonical `PriceItem`
and `pack_size` to how many of the item's unit one billed pack holds, and
an `ItemAlias` remembers the match so the same printed description never
asks twice. When the agent isn't sure it writes an `AgentQuestion`
instead of guessing; the owner (or their bookkeeper) answers it.

Every Monday the engine writes a `PriceDigest`: what changed, what it
cost, what to do. `OutreachDraft` is a message to a supplier rep that the
agent wrote and only a person can send. `ThreadMessage` is the single
conversation between the owner and the agent. `BookkeeperClient` links a
bookkeeper's account to each restaurant it keeps the books for.

Nothing here is a stock count, a par level or an order: the product reads
bills and compares prices, and that is all.
"""

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Date, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.db.types import UTCDateTime

OPEN, ANSWERED, HELD = "open", "answered", "held"
DRAFT, SENT, DISCARDED = "draft", "sent", "discarded"


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PriceItem(Base):
    """A canonical item: what the restaurant buys, whichever supplier prints it however."""

    __tablename__ = "price_items"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str | None] = mapped_column(
        ForeignKey("restaurants.id", name="fk_price_items_restaurant_id_restaurants"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: food, dry, paper, beer, wine, liquor (schemas.CATEGORIES).
    category: Mapped[str] = mapped_column(String(16), default="food", nullable=False)
    #: One `pack_unit` holds `pack_size` of `unit`: "24 ct per case", "40 lb per case".
    unit: Mapped[str] = mapped_column(String(32), default="ct", nullable=False)
    pack_unit: Mapped[str] = mapped_column(String(32), default="case", nullable=False)
    pack_size: Mapped[float] = mapped_column(Float, default=1, nullable=False)
    #: "Not useful": kept out of digests until this date.
    hidden_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: The owner's decision to move the item, so the next digest checks for it instead of repeating itself.
    moved_to_supplier_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("suppliers.id"), nullable=True)
    moved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    #: The stock item this is counted as, so a delivery on an invoice lands in Inventory and a
    #: low stock figure can be reordered at the price the invoices show. Matched by name when
    #: the item is first seen ("auto"); the owner's own choice ("owner") is never overridden.
    inventory_item_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("inventory_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    inventory_link: Mapped[str] = mapped_column(String(8), default="auto", nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)


class Invoice(Base):
    __tablename__ = "supplier_invoices"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str | None] = mapped_column(
        ForeignKey("restaurants.id", name="fk_supplier_invoices_restaurant_id_restaurants"), nullable=True, index=True
    )
    supplier_id: Mapped[str] = mapped_column(String(32), ForeignKey("suppliers.id"), nullable=False, index=True)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    reference: Mapped[str | None] = mapped_column(String(60), nullable=True)
    #: billcom, email, photo, pdf, typed (schemas.SOURCES).
    source: Mapped[str] = mapped_column(String(16), default="typed", nullable=False)
    #: The total as printed, when it was read or typed in. The lines are checked against it.
    total: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: complete, or needs_review: a check failed when it came in (the lines don't add to the
    #: total, a line's arithmetic is off, or the reader was unsure). A needs_review invoice is
    #: kept, and its questions asked, but it stays out of prices until someone says it looks right.
    status: Mapped[str] = mapped_column(String(16), default="complete", nullable=False)
    review_reasons: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    #: Bill.com's id for the bill, so a second pull doesn't make a second invoice.
    external_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    #: The document it was read from (app.documents), and ids in systems it was pushed to ({"qbo": "bill id"}).
    document_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    external_refs: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)

    lines: Mapped[list["InvoiceLine"]] = relationship(
        back_populates="invoice", order_by="InvoiceLine.line_no", cascade="all, delete-orphan"
    )


class InvoiceLine(Base):
    __tablename__ = "supplier_invoice_lines"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str | None] = mapped_column(
        ForeignKey("restaurants.id", name="fk_supplier_invoice_lines_restaurant_id_restaurants"),
        nullable=True, index=True,
    )
    invoice_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("supplier_invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    supplier_id: Mapped[str] = mapped_column(String(32), ForeignKey("suppliers.id"), nullable=False, index=True)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    #: As printed. Never rewritten.
    description: Mapped[str] = mapped_column(String(200), nullable=False)
    qty: Mapped[float] = mapped_column(Float, nullable=False)
    pack_text: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: Per pack, as billed.
    unit_price: Mapped[float] = mapped_column(Float, nullable=False)
    extended: Mapped[float] = mapped_column(Float, nullable=False)
    #: How sure the reader was of this line, 0-1. Null for a line a person typed.
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Set by a match; null while the line is unmatched or waiting on a question.
    item_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("price_items.id"), nullable=True, index=True)
    #: How many of the item's unit one billed pack holds, once known.
    pack_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: What arrived, when it wasn't what was billed. Null means as billed.
    delivered_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Credit the supplier has since issued against this line, in dollars.
    credited: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    #: When what this line delivered was booked into stock (app.prices.stock); null until then.
    stocked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    invoice: Mapped[Invoice] = relationship(back_populates="lines")
    item: Mapped[PriceItem | None] = relationship()


class ItemAlias(Base):
    """A printed description the restaurant has said is a given item, per supplier."""

    __tablename__ = "price_item_aliases"
    __table_args__ = (UniqueConstraint("restaurant_id", "supplier_id", "description_key", name="uq_price_alias"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str | None] = mapped_column(
        ForeignKey("restaurants.id", name="fk_price_item_aliases_restaurant_id_restaurants"), nullable=True, index=True
    )
    supplier_id: Mapped[str] = mapped_column(String(32), ForeignKey("suppliers.id"), nullable=False)
    #: engine.normalize(description).
    description_key: Mapped[str] = mapped_column(String(200), nullable=False)
    item_id: Mapped[str] = mapped_column(String(32), ForeignKey("price_items.id"), nullable=False)
    pack_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)


class AgentQuestion(Base):
    __tablename__ = "agent_questions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str | None] = mapped_column(
        ForeignKey("restaurants.id", name="fk_agent_questions_restaurant_id_restaurants"), nullable=True, index=True
    )
    line_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("supplier_invoice_lines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: match (is this printed line item X?) or pack (is one pack N units?).
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    text: Mapped[str] = mapped_column(String(300), nullable=False)
    #: [{"key": "yes", "label": "Yes"}, ...]
    options: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    proposed_item_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("price_items.id"), nullable=True)
    #: For a match question with no existing candidate: the name a Yes creates.
    proposed_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    proposed_pack_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: The line's dollars on its invoice: what orders the queue.
    dollar_impact: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    #: The Monday of the week it counts against the five-a-week cap.
    week_start: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(10), default=OPEN, nullable=False, index=True)
    answer_key: Mapped[str | None] = mapped_column(String(10), nullable=True)
    answer_text: Mapped[str | None] = mapped_column(String(200), nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    #: By email. Never shown to the owner: a bookkeeper's answer reads as resolved, not as theirs.
    answered_by: Mapped[str | None] = mapped_column(String(254), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)

    line: Mapped[InvoiceLine] = relationship()


class PriceDigest(Base):
    __tablename__ = "price_digests"
    __table_args__ = (UniqueConstraint("restaurant_id", "week_start", name="uq_price_digest_week"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str | None] = mapped_column(
        ForeignKey("restaurants.id", name="fk_price_digests_restaurant_id_restaurants"), nullable=True, index=True
    )
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    total: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    supplier_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: schemas.Card dicts, in digest order, every one of them (the page hides past ten).
    cards: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    invoices_through: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: When this week's mail went out. Null until it has; the weekly job sends each digest once.
    emailed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    built_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)


class SupplierRep(Base):
    """The rep's name and email, remembered per supplier after the first message."""

    __tablename__ = "supplier_reps"
    __table_args__ = (UniqueConstraint("restaurant_id", "supplier_id", name="uq_supplier_rep"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str | None] = mapped_column(
        ForeignKey("restaurants.id", name="fk_supplier_reps_restaurant_id_restaurants"), nullable=True, index=True
    )
    supplier_id: Mapped[str] = mapped_column(String(32), ForeignKey("suppliers.id"), nullable=False)
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    remembered_on: Mapped[date] = mapped_column(Date, nullable=False)


class OutreachDraft(Base):
    __tablename__ = "outreach_drafts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str | None] = mapped_column(
        ForeignKey("restaurants.id", name="fk_outreach_drafts_restaurant_id_restaurants"), nullable=True, index=True
    )
    supplier_id: Mapped[str] = mapped_column(String(32), ForeignKey("suppliers.id"), nullable=False)
    item_id: Mapped[str] = mapped_column(String(32), ForeignKey("price_items.id"), nullable=False)
    #: price_challenge, credit_claim, short_delivery (schemas.DRAFT_KINDS).
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    to_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    to_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    #: The invoice lines cited, as an attachment list.
    line_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(10), default=DRAFT, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    sent_by: Mapped[str | None] = mapped_column(String(254), nullable=True)

    item: Mapped[PriceItem] = relationship()


class ThreadMessage(Base):
    __tablename__ = "agent_thread_messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str | None] = mapped_column(
        ForeignKey("restaurants.id", name="fk_agent_thread_messages_restaurant_id_restaurants"),
        nullable=True, index=True,
    )
    #: "you" or "agent".
    role: Mapped[str] = mapped_column(String(8), nullable=False)
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    #: schemas.Source_ dicts: the chips.
    sources: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    question_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("agent_questions.id", ondelete="SET NULL"), nullable=True
    )
    draft_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("outreach_drafts.id", ondelete="SET NULL"), nullable=True
    )
    #: The agent run answering it, while it is.
    run_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("agent_runs.id"), nullable=True)
    #: ok, pending, failed.
    status: Mapped[str] = mapped_column(String(10), default="ok", nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False, index=True)

    question: Mapped[AgentQuestion | None] = relationship()
    draft: Mapped[OutreachDraft | None] = relationship()


class BookkeeperClient(Base):
    __tablename__ = "bookkeeper_clients"
    __table_args__ = (UniqueConstraint("user_id", "restaurant_id", name="uq_bookkeeper_client"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    restaurant_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("restaurants.id", ondelete="CASCADE", name="fk_bookkeeper_clients_restaurant_id_restaurants"),
        nullable=False, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
