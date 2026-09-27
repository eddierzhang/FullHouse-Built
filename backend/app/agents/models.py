import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.db.types import UTCDateTime


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AgentDefinition(Base):
    __tablename__ = "agent_definitions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # boss|subagent
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(64), default="claude-opus-5", nullable=False)
    tool_allowlist: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    schedule_cron: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    agent_definition_id: Mapped[str] = mapped_column(String(32), ForeignKey("agent_definitions.id"), nullable=False)
    parent_run_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("agent_runs.id"), nullable=True)
    depth: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    # queued|running|succeeded|failed|cancelled
    trigger_type: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)  # manual|scheduled|delegated
    input: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)

    agent_definition: Mapped[AgentDefinition] = relationship()
    events: Mapped[list["AgentEvent"]] = relationship(
        back_populates="run", order_by="AgentEvent.seq", cascade="all, delete-orphan"
    )
    child_runs: Mapped[list["AgentRun"]] = relationship()


class AgentEvent(Base):
    __tablename__ = "agent_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), ForeignKey("agent_runs.id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)
    type: Mapped[str] = mapped_column(String(24), nullable=False)
    # log|tool_call|tool_result|status_change|delegation
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    run: Mapped[AgentRun] = relationship(back_populates="events")


class AgentAction(Base):
    __tablename__ = "agent_actions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String(32), ForeignKey("agent_runs.id"), nullable=False)
    agent_definition_id: Mapped[str] = mapped_column(String(32), ForeignKey("agent_definitions.id"), nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)  # pending|approved|rejected
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # What approving this action actually did, as reported by its applier.
    applied_result: Mapped[str | None] = mapped_column(Text, nullable=True)
