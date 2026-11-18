"""The two restaurants of the preview on 8090 (`python dev.py margin-labor`), each with every feature switched on:

- Corner Grill (demo@example.com): a small sample restaurant. Four dishes, eight ingredients from one supplier, nine
  staff, ten weeks of sales. The same dishes, prices, stock, people and sales on every page (`fixtures.seed_books`).
- Your Restaurant (empty@example.com): nothing in it. An empty slate to fill in.

The seeding itself lives in `app.margin_labor.preview`, which the deployed demo also uses for Corner Grill.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.bootstrap import setup  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.margin_labor import preview  # noqa: E402


def seed() -> None:
    setup()
    db = SessionLocal()
    try:
        preview.seed_empty(db)
        if preview.seed_sample(db) is None:
            print("The preview restaurants are already present, skipping")
            return
        print(f"Seeded Corner Grill ({preview.SAMPLE[0]}) and an empty restaurant ({preview.EMPTY[0]}); password {preview.PASSWORD}")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
