"""Print a one-time password reset link for an account -- for an owner locked out.

Owners and admins issue reset links for their team from the Profile page,
but no one can issue one for an owner. Run this from a machine that can
reach the database, with the same DATABASE_URL the site uses:

    DATABASE_URL=postgresql://... python scripts/reset_link.py owner@example.com [https://your-site]

The link works once, for an hour. Anyone holding it can take the account
over, so open it yourself and don't paste it anywhere. Two-step sign-in,
if the account has it, still applies after the reset.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth import events  # noqa: E402
from app.auth.models import User  # noqa: E402
from app.auth.routes import issue_reset  # noqa: E402
from app.bootstrap import setup  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402


def main(email: str, site: str) -> int:
    setup()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email.strip().lower()).first()
        if user is None:
            print(f"No account with the email {email}.", file=sys.stderr)
            return 1
        token, reset = issue_reset(db, user, issued_by=None)
        events.record(db, "reset_link_issued", email=user.email, restaurant_id=user.restaurant_id,
                      detail={"from": "command line"})
        print(f"{site.rstrip('/')}/#reset/{token}")
        print(f"Works once, until {reset.expires_at:%Y-%m-%d %H:%M} UTC.", file=sys.stderr)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) == 3 else os.getenv("PUBLIC_URL", "http://localhost:8080")))
