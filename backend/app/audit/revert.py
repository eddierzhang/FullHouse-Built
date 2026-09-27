"""Undoing a recorded change.

Reverting is possible because every row carries the state on both sides
of the change, so the inverse is already known:

    insert -> delete the row
    update -> restore the `before` values
    delete -> re-create the row from `before`, original key and all

A revert is never a deletion from history. It replays as a fresh
mutation, so the listener records it as its own change and the timeline
shows both the original and its undo.
"""

from datetime import date, datetime, time
from typing import Any

from sqlalchemy import Date, DateTime, Time, inspect
from sqlalchemy.orm import Session

from app.audit.models import DELETE, INSERT, UPDATE, ChangeLog
from app.db.session import Base


class RevertError(Exception):
    """A change cannot be safely reverted."""


class RevertConflict(RevertError):
    """The entity moved on since this change; reverting would clobber it."""


def _entity_classes() -> dict[str, type]:
    """Table name -> mapped class, for resolving `change_log.entity_type`."""
    classes = {}
    for mapper in Base.registry.mappers:
        table = mapper.local_table
        if table is not None:
            classes[table.name] = mapper.class_
    return classes


def _coerce(column, value: Any) -> Any:
    """Turn a JSON-stored value back into what the column expects.

    The log holds dates and times as ISO strings; assigning those back
    verbatim would store the string representation instead of a real
    temporal value.
    """
    if value is None or not isinstance(value, str):
        return value
    column_type = column.type
    if isinstance(column_type, DateTime):
        return datetime.fromisoformat(value)
    if isinstance(column_type, Date):
        return date.fromisoformat(value)
    if isinstance(column_type, Time):
        return time.fromisoformat(value)
    return value


def _columns_by_key(mapper) -> dict:
    return {attr.key: attr.columns[0] for attr in mapper.column_attrs}


def _primary_key(mapper, entity_id: str) -> tuple:
    """Rebuild a primary key tuple from the stored ':'-joined string."""
    parts = entity_id.split(":")
    if len(parts) != len(mapper.primary_key):
        raise RevertError(f"Cannot parse entity id {entity_id!r} for {mapper.local_table.name}")
    key = []
    for column, part in zip(mapper.primary_key, parts):
        try:
            python_type = column.type.python_type
        except NotImplementedError:
            python_type = str
        if python_type is int:
            try:
                part = int(part)
            except ValueError as exc:
                raise RevertError(f"Primary key segment {part!r} is not an integer") from exc
        key.append(part)
    return tuple(key)


def _current_values(entity, keys) -> dict:
    from app.audit.listener import _jsonable

    return {key: _jsonable(getattr(entity, key)) for key in keys}


def _check_unchanged(entity, expected: dict) -> None:
    """Refuse to revert a change that newer edits have already superseded."""
    current = _current_values(entity, expected.keys())
    drifted = {
        key: {"expected": expected[key], "current": current[key]}
        for key in expected
        if current[key] != expected[key]
    }
    if drifted:
        raise RevertConflict(
            f"Entity has changed since this change was recorded: {drifted}. "
            f"Re-check the timeline, or pass force=true to overwrite anyway."
        )


def revert_change(db: Session, change: ChangeLog, *, force: bool = False) -> str:
    """Apply the inverse of `change`. Returns a description; does not commit."""
    entity_class = _entity_classes().get(change.entity_type)
    if entity_class is None:
        raise RevertError(f"Unknown entity type {change.entity_type!r}")

    mapper = inspect(entity_class)
    columns = _columns_by_key(mapper)
    pk = _primary_key(mapper, change.entity_id)
    entity = db.get(entity_class, pk if len(pk) > 1 else pk[0])
    label = f"{change.entity_type}:{change.entity_id}"

    if change.operation == INSERT:
        if entity is None:
            raise RevertConflict(f"{label} no longer exists; nothing to undo")
        if not force:
            _check_unchanged(entity, change.after or {})
        db.delete(entity)
        return f"Deleted {label}, undoing its creation"

    if change.operation == UPDATE:
        if entity is None:
            raise RevertConflict(f"{label} no longer exists; cannot restore its prior values")
        if not force:
            _check_unchanged(entity, change.after or {})
        restored = {}
        for key, value in (change.before or {}).items():
            column = columns.get(key)
            if column is None:
                raise RevertError(f"Column {key!r} no longer exists on {change.entity_type}")
            setattr(entity, key, _coerce(column, value))
            restored[key] = value
        db.add(entity)
        return f"Restored {label} fields {sorted(restored)} to their prior values"

    if change.operation == DELETE:
        if entity is not None:
            raise RevertConflict(f"{label} already exists; cannot re-create it")
        values = {
            key: _coerce(columns[key], value)
            for key, value in (change.before or {}).items()
            if key in columns
        }
        db.add(entity_class(**values))
        return f"Re-created {label} from its pre-deletion state"

    raise RevertError(f"Unknown operation {change.operation!r}")
