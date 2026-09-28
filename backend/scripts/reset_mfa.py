"""Clear an account's two-step sign-in -- for an owner who lost their phone and backup codes.

Owners reset it for admins from the Profile page, but no one can for an
owner. Run this from a machine that can reach the database, with the same
DATABASE_URL (and SECRET_KEY) the site uses:

    DATABASE_URL=postgresql://... python scripts/reset_mfa.py owner@example.com

It also ends the account's sessions. The owner then signs in with their
password and sets two-step sign-in up again on a new phone.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth import events  # noqa: E402
from app.auth.models import AuthSession, User  # noqa: E402
from app.auth.routes import clear_mfa  # noqa: E402
from app.bootstrap import setup  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402


def main(email: str) -> int:
    setup()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email.strip().lower()).first()
        if user is None:
            print(f"No account with the email {email}.", file=sys.stderr)
            return 1
        clear_mfa(user)
        db.query(AuthSession).filter(AuthSession.user_id == user.id).delete()
        db.commit()
        events.record(db, "mfa_reset", email=user.email, restaurant_id=user.restaurant_id,
                      detail={"from": "command line"})
        print(f"Two-step sign-in cleared for {user.email}. They set it up again at their next sign-in.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
