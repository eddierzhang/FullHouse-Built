from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.db.types import UTCDateTime


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Feedback(Base):
    """Something a person told us from the app's feedback button -- a problem,
    a confusing screen, a wish. Read by the restaurant's owners and admins on
    the Help page, and by whoever runs the pilot."""

    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    restaurant_id: Mapped[str | None] = mapped_column(
        ForeignKey("restaurants.id", name="fk_feedback_restaurant_id_restaurants"), nullable=True, index=True
    )
    #: Who sent it, by email -- as the change history names people.
    sent_by: Mapped[str] = mapped_column(String(254), nullable=False)
    #: Where in the app they were, e.g. "inventory".
    page: Mapped[str | None] = mapped_column(String(100), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False, index=True)
