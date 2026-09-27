from datetime import datetime, timezone

from sqlalchemy import JSON, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.db.types import UTCDateTime

INSERT = "insert"
UPDATE = "update"
DELETE = "delete"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ChangeLog(Base):
    """One row per mutation of a tracked entity.

    Deliberately has no foreign keys. An audit row has to outlive the
    thing it describes -- including the deletes it records -- so the
    entity, run and action ids are stored as plain values.
    """

    __tablename__ = "change_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)

    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)  # table name
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[str] = mapped_column(String(16), nullable=False)  # insert|update|delete

    # For an update these hold only the columns that actually changed; for an
    # insert `before` is null, and for a delete `after` is null.
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    changed_fields: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)  # human|agent|system
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    agent_action_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_change_log_entity", "entity_type", "entity_id"),
        Index("ix_change_log_ts", "ts"),
        Index("ix_change_log_agent_run_id", "agent_run_id"),
    )
