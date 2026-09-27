from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """A timestamp that always comes back from the database in UTC, tz-aware.

    Postgres stores the offset and returns aware values. SQLite keeps no
    offset at all and returns naive ones, which the API then serialised
    without a "Z" -- so a browser west of UTC read every timestamp as local
    time and showed runs from minutes ago as "in 7 hours". Every value is
    written as UTC and read back with UTC attached, on both databases.

    Same DDL as DateTime(timezone=True), so no migration is involved.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
