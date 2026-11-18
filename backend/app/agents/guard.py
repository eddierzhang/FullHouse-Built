"""The agent writes only proposals: enforced in the data layer, not the prompt.

While a tool runs (`tool_scope`), a before-flush listener refuses any insert, update or
delete outside the few tables a tool may touch: its proposals (`agent_actions`), its own
run's events and messages, and the platform's logs. A tool that tries to change stock, a
shift, a menu price or an invoice raises `ForbiddenAgentWrite` before the statement is
sent, whatever the tool's code says. The forbidden-write test iterates every tenant table.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

#: Tables an agent tool may write: its proposals, its own words (the conversation, and its
#: summaries of customer requests, which managers read and nothing acts on), its run's events,
#: and the platform's logs. Nothing here is stock, money, a schedule, a menu or an invoice.
ALLOWED_TABLES = frozenset({
    "agent_actions", "agent_events", "agent_runs", "agent_thread_messages",
    "customer_service_reviews", "customer_action_items",
    "change_log", "events", "jobs",
})

_in_tool: ContextVar[str | None] = ContextVar("agent_tool_scope", default=None)


class ForbiddenAgentWrite(RuntimeError):
    """A tool tried to write a table only people (or appliers acting for them) may write."""


@contextmanager
def tool_scope(tool_name: str) -> Iterator[None]:
    token = _in_tool.set(tool_name)
    try:
        yield
    finally:
        _in_tool.reset(token)


def in_tool() -> str | None:
    return _in_tool.get()


def _table(obj) -> str | None:
    table = inspect(obj).mapper.local_table
    return table.name if table is not None else None


def refuse_forbidden_writes(session: Session, flush_context, instances) -> None:
    tool = _in_tool.get()
    if tool is None:
        return
    touched = list(session.new) + list(session.deleted)
    touched += [obj for obj in session.dirty if session.is_modified(obj, include_collections=False)]
    for obj in touched:
        table = _table(obj)
        if table and table not in ALLOWED_TABLES:
            raise ForbiddenAgentWrite(
                f"Tool {tool!r} tried to write {table}. The agent proposes; people approve. "
                "Make it a proposal (agent_actions) instead."
            )


_installed = False


def install() -> None:
    global _installed
    if _installed:
        return
    event.listen(Session, "before_flush", refuse_forbidden_writes)
    _installed = True


def guarded(tool):
    """Wrap a `@beta_tool` so its handler runs inside `tool_scope`."""
    original = tool.call

    def call(arguments):
        with tool_scope(tool.name):
            return original(arguments)

    tool.call = call
    return tool
