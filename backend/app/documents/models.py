"""The document registry. Invoices keep their own tables and link back through `invoices.document_id`."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.db.types import UTCDateTime


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


DOC_TYPES = ("invoice", "price_list", "recipe", "prep_sheet", "license", "permit", "inspection", "warranty", "other")
RECEIVED, EXTRACTED, NEEDS_REVIEW, COMPLETE = "received", "extracted", "needs_review", "complete"


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (Index("ix_documents_restaurant_created", "restaurant_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", name="fk_documents_restaurant"), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(16), default="other", nullable=False)
    #: How sure the classifier was of `doc_type`; under 0.9 the reviewer picks.
    type_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: The key in app.storage; the database never holds the bytes.
    file_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="upload", nullable=False)  # upload|email|connector
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default=RECEIVED, nullable=False)
    extracted: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: What came of it: the invoice, the proposal.
    result_table: Mapped[str | None] = mapped_column(String(40), nullable=True)
    result_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now, nullable=False)
