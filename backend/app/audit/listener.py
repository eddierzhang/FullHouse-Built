"""Automatic change capture.

Hooks the ORM rather than individual crud functions, so a mutation is
recorded no matter which path made it -- a REST handler, an agent tool,
an applier, or a one-off script. The alternative (hand-placed
`log_change()` calls) drifts out of sync the moment someone adds a code
path and forgets one.

Capture is split across two events because neither alone is sufficient:

* `before_flush` is the only point where the *old* value of a column is
  still knowable. `Session.commit()` expires attributes, so by the time
  a later `item.qty = 42` is flushed, SQLAlchemy holds no loaded value
  to diff against and has to re-read the row -- which is only the
  pre-change value before the UPDATE runs.
* `after_flush` is the only point where an inserted row knows its
  primary key, since Python-side defaults (`default=_uuid`) are applied
  during the flush itself.

So updates and deletes are collected in the first, inserts in the
second, and everything is written once at the end of the flush.
"""

from datetime import date, datetime, time, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Mapper, Session

from app.audit.context import get_actor, get_note
from app.audit.models import DELETE, INSERT, UPDATE, ChangeLog
from app.db.session import Base

#: Tables that are themselves append-only logs. Auditing them would
#: double-record history and, for change_log, recurse.
EXCLUDED_TABLES = frozenset({"change_log", "agent_events"})

#: Key under which half-built rows wait on `Session.info` between the two events.
_PENDING = "_audit_pending_rows"

_installed = False


def _jsonable(value: Any) -> Any:
    """Coerce a column value into something the JSON column can store."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return _jsonable(value.value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)


def _entity_id(state, obj) -> str:
    """Stringified primary key, joined on ':' for composite keys."""
    identity = state.identity
    if identity is None:
        mapper = state.mapper
        identity = tuple(
            getattr(obj, mapper.get_property_by_column(col).key, None) for col in mapper.primary_key
        )
    return ":".join(str(v) for v in identity if v is not None)


def _snapshot(state, obj, *, allow_load: bool) -> dict:
    """Every column value on `obj`.

    `allow_load` is false after the flush has run: emitting a SELECT
    there would either deadlock or read back the row we just wrote. It is
    true before the flush, where reading an expired row is both safe and
    the only way to see a deleted object's final state.
    """
    keys = [attr.key for attr in state.mapper.column_attrs]
    if not allow_load:
        unloaded = state.unloaded
        keys = [key for key in keys if key not in unloaded]
    return {key: _jsonable(getattr(obj, key)) for key in keys}


def _diff(state) -> tuple[dict, dict, list[str]]:
    """Per-column before/after for an updated object.

    Uses `load_history()` rather than `history`: the latter refuses to
    emit a loader, which means an attribute assigned after a commit
    expired it reports its old value as None.
    """
    before: dict = {}
    after: dict = {}
    for attr in state.mapper.column_attrs:
        history = state.attrs[attr.key].load_history()
        if not history.has_changes():
            continue
        old = _jsonable(history.deleted[0]) if history.deleted else None
        new = _jsonable(history.added[0]) if history.added else None
        if old == new:
            continue
        before[attr.key] = old
        after[attr.key] = new
    return before, after, sorted(before)


def _tracked(obj) -> bool:
    table = inspect(obj).mapper.local_table
    return table is not None and table.name not in EXCLUDED_TABLES


def _enable_active_history(mapper: Mapper) -> None:
    """Make SQLAlchemy fetch a column's prior value when it is overwritten.

    Without this, assigning to an attribute that isn't currently loaded --
    which is every attribute right after a `commit()`, since commit
    expires them -- records the old value as None. SQLAlchemy genuinely
    does not know it: `committed_state` holds NO_VALUE and no amount of
    history coaxing will recover it, because the loader won't overwrite
    the pending assignment.

    The cost is one SELECT the first time such an attribute is assigned,
    to unexpire the row. An object that is already loaded costs nothing
    extra, which is the common case.
    """
    table = mapper.local_table
    if table is None or table.name in EXCLUDED_TABLES:
        return
    manager = mapper.class_manager
    for attr in mapper.column_attrs:
        # The flag lives on the instrumented attribute's impl, not on the
        # ColumnProperty -- and that impl is only built once mappers are
        # configured, which is why this runs on `after_configured`.
        impl = manager[attr.key].impl
        if impl is not None:
            impl.active_history = True


def _row(obj, operation: str, before: dict | None, after: dict | None, changed: list[str]) -> dict:
    actor = get_actor()
    state = inspect(obj)
    return {
        "ts": datetime.now(timezone.utc),
        "entity_type": state.mapper.local_table.name,
        "entity_id": _entity_id(state, obj),
        "operation": operation,
        "before": before,
        "after": after,
        "changed_fields": changed,
        "actor_type": actor.type,
        "actor_id": actor.id,
        "agent_run_id": actor.agent_run_id,
        "agent_action_id": actor.agent_action_id,
        "note": get_note(),
    }


def collect_before_flush(session: Session, flush_context, instances) -> None:
    """Capture updates and deletes while their prior state is still readable."""
    pending = session.info.setdefault(_PENDING, [])

    for obj in session.dirty:
        if not _tracked(obj):
            continue
        before, after, changed = _diff(inspect(obj))
        if not changed:
            continue  # session.dirty over-reports; the diff is the real test
        pending.append(_row(obj, UPDATE, before, after, changed))

    for obj in session.deleted:
        if not _tracked(obj):
            continue
        state = inspect(obj)
        pending.append(_row(obj, DELETE, _snapshot(state, obj, allow_load=True), None, []))


def write_after_flush(session: Session, flush_context) -> None:
    """Add the inserts -- now that they have primary keys -- and persist."""
    rows = session.info.pop(_PENDING, [])

    for obj in session.new:
        if not _tracked(obj):
            continue
        state = inspect(obj)
        rows.append(_row(obj, INSERT, None, _snapshot(state, obj, allow_load=False), []))

    if not rows:
        return

    # Core insert on the flush's own connection: it lands in the same
    # transaction as the changes it describes, but bypasses the unit of
    # work -- so it neither re-triggers this listener nor gets deferred
    # to a later flush the way session.add() inside after_flush would.
    session.connection().execute(ChangeLog.__table__.insert(), rows)


def discard_pending(session: Session, *args) -> None:
    """Drop half-built rows when a flush never completes."""
    session.info.pop(_PENDING, None)


def install() -> None:
    """Register the listeners. Idempotent.

    Call once at import time, after the model modules are imported and
    before any session is opened.
    """
    global _installed
    if _installed:
        return
    event.listen(Session, "before_flush", collect_before_flush)
    event.listen(Session, "after_flush", write_after_flush)
    event.listen(Session, "after_soft_rollback", discard_pending)

    # Covers mappers configured from here on...
    event.listen(Mapper, "after_configured", _on_after_configured)
    # ...and any already configured by the time we got here. Touching
    # `.mappers` forces configuration, so this also fires the above.
    _on_after_configured()

    _installed = True


def _on_after_configured() -> None:
    for mapper in Base.registry.mappers:
        _enable_active_history(mapper)


def uninstall() -> None:
    """Drop the listeners again -- used by tests that assert on their absence."""
    global _installed
    if not _installed:
        return
    event.remove(Session, "before_flush", collect_before_flush)
    event.remove(Session, "after_flush", write_after_flush)
    event.remove(Session, "after_soft_rollback", discard_pending)
    event.remove(Mapper, "after_configured", _on_after_configured)
    _installed = False
