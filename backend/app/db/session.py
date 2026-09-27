import sqlite3
from collections.abc import Generator
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


def normalize_database_url(url: str) -> str:
    """Point bare Postgres URLs at the psycopg 3 driver.

    Hosts such as Render and Neon hand out `postgres://` or `postgresql://`
    URLs, which SQLAlchemy would otherwise route to psycopg2 -- not installed.
    """
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def engine_options(url: str) -> dict[str, Any]:
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    if url.startswith("postgresql"):
        # Sessions at zero offset: timestamps are bucketed into days in Python,
        # and a server in another zone would shift orders across midnight.
        # "GMT" rather than "UTC": it is built into every Postgres, whereas
        # "UTC" needs the timezone database, which some builds ship without.
        return {"connect_args": {"options": "-c timezone=GMT"}, "pool_pre_ping": True}
    return {}


DATABASE_URL = normalize_database_url(settings.database_url)
engine = create_engine(DATABASE_URL, **engine_options(DATABASE_URL))
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@event.listens_for(Engine, "connect")
def _enforce_sqlite_foreign_keys(dbapi_connection, connection_record):
    """SQLite ignores foreign keys unless asked, per connection.

    Left off, deleting a referenced row silently orphans whatever points
    at it -- a menu item removed out from under the order lines that
    record its sales. Reverting an insert deletes rows, so this needs to
    fail loudly rather than quietly. Postgres always enforces them.
    """
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
