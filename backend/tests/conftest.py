import os
import tempfile

# Must run before anything imports app.config. The app's own engine -- used
# by startup recovery and background runs -- would otherwise point at the
# developer's real app.db, and running the suite while the app is up would
# mark its in-flight agent runs as failed.
#
# TEST_DATABASE_URL runs the whole suite against that database instead (CI
# uses it for Postgres); otherwise a throwaway SQLite file isolates it.
os.environ["SCHEDULER_ENABLED"] = "false"  # tests drive scheduled jobs directly
os.environ["ALLOW_SIGNUP"] = "true"  # any number of restaurants may sign up; test_auth covers it turned off
os.environ["REQUIRE_MFA"] = "false"  # test_mfa turns it on; elsewhere owners sign in with a password alone

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if TEST_DATABASE_URL:
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
else:
    _scratch = os.path.join(tempfile.mkdtemp(prefix="fullhouse-tests-"), "app.db")
    os.environ["DATABASE_URL"] = f"sqlite:///{_scratch}"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

# Imported for their side effect of registering tables on Base.metadata.
from app.agents import models as agent_models  # noqa: E402,F401
from app.audit import listener as audit_listener  # noqa: E402
from app.audit import models as audit_models  # noqa: E402,F401
from app.auth import models as auth_models  # noqa: E402,F401
from app.support import models as support_models  # noqa: E402,F401
from app.db import session as app_session  # noqa: E402
from app.db.session import Base, get_db  # noqa: E402
from app.restaurant import models as restaurant_models  # noqa: E402,F401
from app.restaurant import inventory as inventory_listener  # noqa: E402

# The listener is global to the Session class, so install it once for the
# whole test session exactly as the app does at import time.
audit_listener.install()
inventory_listener.install()

# The app-level engine needs tables even in SQLite mode, since startup
# recovery queries it whenever a test starts the app.
Base.metadata.create_all(app_session.engine)

USING_POSTGRES = app_session.DATABASE_URL.startswith("postgresql")


@pytest.fixture
def engine():
    if USING_POSTGRES:
        # One shared server: rebuild the schema so every test starts empty.
        eng = app_session.engine
        Base.metadata.drop_all(eng)
        Base.metadata.create_all(eng)
        yield eng
        return

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # one shared in-memory db across connections
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


#: The account `client` is signed in as; changes it makes carry this email.
TEST_USER_EMAIL = "eddie@example.com"
TEST_USER_PASSWORD = "correct-horse"


@pytest.fixture
def anon_client(db):
    """TestClient sharing the test's session, so assertions see its writes. Not signed in."""
    from app.auth.routes import failed_accounts, failed_logins
    from app.main import app

    failed_logins.clear()
    failed_accounts.clear()
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def client(anon_client, db):
    """TestClient signed in as TEST_USER_EMAIL: an owner with no restaurant yet.

    Signing up would give the account a restaurant of its own (test_auth
    covers that). Most tests want the state before one exists -- records
    that belong to no restaurant, which this account sees and its first
    setup step claims -- so the account is made directly.
    """
    from datetime import datetime, timedelta, timezone

    from app.auth.deps import SESSION_COOKIE
    from app.auth.models import AuthSession, User
    from app.auth.security import hash_password, new_session_token, token_id

    user = User(email=TEST_USER_EMAIL, name="Eddie", password_hash=hash_password(TEST_USER_PASSWORD))
    db.add(user)
    db.flush()
    # A session without a sign-in, so the security log starts empty, as it does after signing up.
    token = new_session_token()
    db.add(AuthSession(id=token_id(token), user_id=user.id, expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
    db.commit()
    anon_client.cookies.set(SESSION_COOKIE, token)
    return anon_client


@pytest.fixture
def changes(db):
    """Callable returning change_log rows, newest last."""
    from app.audit.models import ChangeLog

    def _changes(entity_type: str | None = None, operation: str | None = None):
        query = db.query(ChangeLog)
        if entity_type:
            query = query.filter(ChangeLog.entity_type == entity_type)
        if operation:
            query = query.filter(ChangeLog.operation == operation)
        return query.order_by(ChangeLog.id).all()

    return _changes


@pytest.fixture
def threaded_db(engine, tmp_path):
    """A session on an engine that real concurrent connections can share.

    The default test engine is one in-memory SQLite connection, which two
    threads must never use at once.
    """
    if USING_POSTGRES:
        eng = engine
    else:
        eng = create_engine(f"sqlite:///{tmp_path / 'parallel.db'}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(eng)
    session = sessionmaker(bind=eng, autoflush=False, autocommit=False)()
    yield session
    session.close()
    if not USING_POSTGRES:
        eng.dispose()
