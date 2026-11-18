"""The two restaurants of the preview on 8090 (`python dev.py margin-labor`), each with every feature switched on:

- Corner Grill (demo@example.com): a small sample restaurant. Four dishes, eight ingredients from one supplier, nine
  staff, ten weeks of sales. The same dishes, prices, stock, people and sales on every page (`fixtures.seed_books`).
- Your Restaurant (empty@example.com): nothing in it. An empty slate to fill in.
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents.definitions import AGENT_DEF  # noqa: E402
from app.agents.models import AgentDefinition  # noqa: E402
from app.auth.models import User  # noqa: E402
from app.auth.security import hash_password  # noqa: E402
from app.bootstrap import setup  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.demo import DEMO_PROFILE  # noqa: E402
from app.margin_labor import demand, fixtures, labor, labor_read, menu_costs, schedule, usage  # noqa: E402
from app.platform import flags  # noqa: E402
from app.restaurant.models import Restaurant  # noqa: E402

PASSWORD = "demo-kitchen"
SAMPLE = ("demo@example.com", "Corner Grill")
EMPTY = ("empty@example.com", "Your Restaurant")


def _restaurant(db, email: str, name: str) -> Restaurant:
    restaurant = Restaurant(**{**DEMO_PROFILE, "name": name}, setup_completed_at=datetime.now(timezone.utc))
    db.add(restaurant)
    db.flush()
    db.add(User(email=email, name="Sample Owner", password_hash=hash_password(PASSWORD), restaurant_id=restaurant.id))
    flags.enable_all(db, restaurant.id)
    for flag in (*flags.ML_FLAGS, flags.V2_CONNECT, flags.V2_DOCUMENTS):
        flags.set_flag(db, restaurant.id, flag, True)
    flags.set_flag(db, restaurant.id, flags.V3_VOICE, False)
    flags.set_flag(db, restaurant.id, "ml_channels", False)  # Channels is out of the app for now: no findings, no proposals
    db.commit()
    return restaurant


def seed() -> None:
    setup()
    db = SessionLocal()
    try:
        if db.query(User).filter(User.email == SAMPLE[0]).first():
            print("The preview restaurants are already present, skipping")
            return
        if db.query(AgentDefinition).filter(AgentDefinition.key == AGENT_DEF["key"]).first() is None:
            # Proposals are filed under the agent: it has to exist before anything is proposed.
            db.add(AgentDefinition(role="main", model="claude-opus-5", enabled=True, **AGENT_DEF))
            db.flush()
        _restaurant(db, *EMPTY)
        rid = _restaurant(db, *SAMPLE).id
        service = labor.Service(db, rid)
        made = fixtures.seed_preview(db, rid, service.today, service.conf.timezone, datetime.now(timezone.utc).astimezone(service.tz))
        db.commit()
        menu_costs.recost(db, rid)
        db.commit()
        fixtures.seed_books(db, rid, made, service.today, service.conf.timezone)
        db.commit()
        usage.run(db, rid)
        demand.forecast_items(db, rid)
        db.commit()
        labor.forecast_labor(db, rid)
        db.commit()
        schedule.generate(db, rid, labor_read.default_week(service.today), seconds=12)
        db.commit()
        print(f"Seeded Corner Grill ({SAMPLE[0]}) and an empty restaurant ({EMPTY[0]}); password {PASSWORD}")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
