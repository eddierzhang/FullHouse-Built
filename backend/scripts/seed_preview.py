"""The restaurant for the preview on 8090 (`python dev.py margin-labor`): every feature switched on and nothing in
it, behind a single sign-in. No recipes, dishes, invoices, stock, suppliers, sales or staff: an empty slate to fill in.

`PREVIEW_SAMPLE=1` fills it with sample data instead (every module populated), as it was before 2026-11-18."""

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents.definitions import AGENT_DEF  # noqa: E402
from app.agents.models import AgentDefinition  # noqa: E402
from app.auth.models import User  # noqa: E402
from app.auth.security import hash_password  # noqa: E402
from app.bootstrap import setup  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.demo import DEMO_PROFILE  # noqa: E402
from app.margin_labor import channels, demand, fixtures, labor, labor_read, menu_costs, schedule, usage  # noqa: E402
from app.platform import flags  # noqa: E402
from app.restaurant.models import Restaurant  # noqa: E402

EMAIL = "demo@example.com"
PASSWORD = "demo-kitchen"
NAME = "Your Restaurant"


def seed() -> None:
    setup()
    db = SessionLocal()
    try:
        if db.query(User).filter(User.email == EMAIL).first():
            print("The preview restaurant is already present, skipping")
            return
        if db.query(AgentDefinition).filter(AgentDefinition.key == AGENT_DEF["key"]).first() is None:
            # Proposals are filed under the agent: it has to exist before anything is proposed.
            db.add(AgentDefinition(role="main", model="claude-opus-5", enabled=True, **AGENT_DEF))
            db.flush()
        restaurant = Restaurant(**{**DEMO_PROFILE, "name": NAME}, setup_completed_at=datetime.now(timezone.utc))
        db.add(restaurant)
        db.flush()
        db.add(User(email=EMAIL, name="Sample Owner", password_hash=hash_password(PASSWORD), restaurant_id=restaurant.id))
        flags.enable_all(db, restaurant.id)
        for flag in (*flags.ML_FLAGS, flags.V2_CONNECT, flags.V2_DOCUMENTS):
            flags.set_flag(db, restaurant.id, flag, True)
        flags.set_flag(db, restaurant.id, flags.V3_VOICE, False)
        db.commit()
        if os.getenv("PREVIEW_SAMPLE", "") not in ("1", "true"):
            print(f"Seeded an empty restaurant: {EMAIL} / {PASSWORD}")
            return
        service = labor.Service(db, restaurant.id)
        fixtures.seed_preview(db, restaurant.id, service.today, service.conf.timezone, datetime.now(timezone.utc).astimezone(service.tz))
        db.commit()
        rid = restaurant.id
        menu_costs.recost(db, rid)
        db.commit()
        usage.run(db, rid)
        channels.run(db, rid)
        demand.forecast_items(db, rid)
        db.commit()
        labor.forecast_labor(db, rid)
        db.commit()
        schedule.generate(db, rid, labor_read.default_week(service.today), seconds=12)
        db.commit()
        print(f"Seeded the preview restaurant: {EMAIL} / {PASSWORD}")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
