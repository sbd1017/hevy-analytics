"""Time handling. Everything is stored in UTC; days and weeks are local (Europe/London by default, DST-aware)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc


def parse_ts(value: str | None) -> datetime | None:
    """Parse Hevy's ISO-8601 timestamps ('...Z', '+00:00', with or without fractions) to an aware UTC datetime."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:                       # Hevy always sends an offset; a naive value is treated as UTC
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def iso(dt: datetime) -> str:
    """UTC ISO-8601 with milliseconds and a Z, e.g. 2026-10-19T22:01:09.123Z (sorts correctly as text)."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def iso_s(dt: datetime) -> str:
    """UTC ISO-8601 to the second, e.g. 2026-10-19T22:01:09Z."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def utcnow() -> datetime:
    return datetime.now(UTC)


def local_date(dt: datetime, tz: str) -> str:
    return dt.astimezone(ZoneInfo(tz)).date().isoformat()


def later(a: str | None, b: str | None) -> bool:
    """True when timestamp a is strictly later than b (None counts as the beginning of time)."""
    da, db = parse_ts(a), parse_ts(b)
    if da is None:
        return False
    if db is None:
        return True
    return da > db


def minus(value: str, seconds: int) -> str:
    dt = parse_ts(value)
    return iso_s(dt - timedelta(seconds=seconds)) if dt else value
