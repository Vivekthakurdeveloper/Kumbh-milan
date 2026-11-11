from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

CASE_TZ = ZoneInfo("Asia/Kolkata")


def utcnow() -> datetime:
    return datetime.now(UTC)


def kolkata_day_start(day: date) -> datetime:
    """Inclusive start of a calendar day in Asia/Kolkata, as a timezone-aware datetime."""
    return datetime(day.year, day.month, day.day, tzinfo=CASE_TZ)


def kolkata_day_end_exclusive(day: date) -> datetime:
    """Exclusive upper bound for an inclusive Asia/Kolkata calendar end date (start of next day)."""
    return kolkata_day_start(day) + timedelta(days=1)
