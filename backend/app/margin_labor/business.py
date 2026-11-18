"""The business date: the trading day a moment belongs to (spec section 4).

A sale at 01:30 on Saturday belongs to Friday night. It is computed once, at ingestion, from the
restaurant's own time zone and the hour its trading day starts:

    local_time    = timestamp converted to the restaurant's time zone
    business_date = date(local_time - business_day_start)
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def _start(business_day_start: str) -> timedelta:
    hours, _, minutes = (business_day_start or "04:00").partition(":")
    return timedelta(hours=int(hours), minutes=int(minutes or 0))


def business_date(moment: datetime, tz: str | None, business_day_start: str = "04:00") -> date:
    """The trading day of `moment`. A naive `moment` is taken as UTC."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (moment.astimezone(zone(tz)) - _start(business_day_start)).date()


def day_bounds(day: date, tz: str | None, business_day_start: str = "04:00") -> tuple[datetime, datetime]:
    """The moments (UTC) a business date starts and ends: [start, end)."""
    opens = datetime.combine(day, time(0, 0), tzinfo=zone(tz)) + _start(business_day_start)
    return opens.astimezone(timezone.utc), (opens + timedelta(days=1)).astimezone(timezone.utc)


def interval_start(moment: datetime, minutes: int = 15) -> datetime:
    """The start of the interval `moment` falls in, in UTC. Intervals are aligned to the hour."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    return moment.replace(minute=moment.minute - moment.minute % minutes, second=0, microsecond=0)
