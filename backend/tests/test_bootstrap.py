"""Entry points other than the API must register change tracking too.

The seed script imports `SessionLocal` directly and never touches
`app.main`, so before `app.bootstrap` existed it wrote every seeded row
with no audit record -- silently, and only outside the API.
"""

import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_seed_script_records_its_writes(tmp_path):
    db_path = tmp_path / "seeded.db"
    env = {
        **dict(__import__("os").environ),
        "DATABASE_URL": f"sqlite:///{db_path}",
    }

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT, env=env, check=True, capture_output=True,
    )
    seeded = subprocess.run(
        [sys.executable, "scripts/seed_data.py"],
        cwd=BACKEND_ROOT, env=env, check=True, capture_output=True, text=True,
    )
    assert "Seeded" in seeded.stdout

    from sqlalchemy import create_engine, text

    with create_engine(f"sqlite:///{db_path}").connect() as conn:
        changes = conn.execute(text("SELECT COUNT(*) FROM change_log")).scalar()
        menu_items = conn.execute(text("SELECT COUNT(*) FROM menu_items")).scalar()
        actors = conn.execute(text("SELECT DISTINCT actor_type FROM change_log")).scalars().all()

    assert menu_items > 0
    assert changes >= menu_items, "seeded rows were written without a change record"
    assert actors == ["system"], "seeding is not attributable to a human or agent"


def test_hosted_postgres_urls_use_the_installed_driver():
    """Render and Neon hand out bare postgres:// URLs; psycopg2 is not installed."""
    from app.db.session import normalize_database_url

    assert normalize_database_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_database_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_database_url("postgresql+psycopg://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_database_url("sqlite:///./app.db") == "sqlite:///./app.db"
