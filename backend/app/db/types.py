from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, Numeric
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


class Dec(TypeDecorator):
    """Money and quantities: exact decimals, `numeric(12,4)` unless told otherwise.

    Postgres stores and returns them exactly. SQLite has no decimal type and SQLAlchemy warns on every
    Decimal it binds there, so on SQLite the value is bound as a float and read back through the scale,
    which is exact to the column's places for any figure a restaurant has. Either way Python only ever
    sees `Decimal`: a float handed in is converted through its string, never used in arithmetic.
    """

    impl = Numeric(12, 4, asdecimal=True)
    cache_ok = True

    def __init__(self, precision: int = 12, scale: int = 4):
        super().__init__()
        self.impl = Numeric(precision, scale, asdecimal=True)
        self._quantum = Decimal(1).scaleb(-scale)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        exact = value if isinstance(value, Decimal) else Decimal(str(value))
        exact = exact.quantize(self._quantum)
        return float(exact) if dialect.name == "sqlite" else exact

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return (value if isinstance(value, Decimal) else Decimal(str(value))).quantize(self._quantum)
