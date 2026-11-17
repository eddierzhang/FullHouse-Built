"""Voice: what was said, what it was taken to mean, and what each restaurant calls its items out loud."""

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.db.types import UTCDateTime


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)



class VoiceCommand(Base):
    """One thing said. Parsed into a preview first; it changes nothing until a person confirms it."""

    __tablename__ = "voice_commands"
    __table_args__ = (Index("ix_voice_commands_restaurant_created", "restaurant_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", name="fk_voice_commands_restaurant"), nullable=False)
    user_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    transcript: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str] = mapped_column(String(16), nullable=False)  # count|waste|receive|ask|navigate|unknown
    parsed: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="previewed", nullable=False)  # previewed|confirmed|discarded|failed
    #: Seconds of audio, when the server transcribed it; null when the browser did.
    seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(10), default="browser", nullable=False)  # browser|server
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class SpokenAlias(Base):
    """What this restaurant says for an item ("mozz", "the big tomatoes"), learned from a correction."""

    __tablename__ = "spoken_aliases"
    __table_args__ = (UniqueConstraint("restaurant_id", "phrase", name="uq_spoken_aliases_phrase"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", name="fk_spoken_aliases_restaurant"), nullable=False)
    phrase: Mapped[str] = mapped_column(String(120), nullable=False)
    inventory_item_id: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
