"""Date ranges and the period they are compared with (docs/METRICS.md, "Comparing periods").

All dates are local calendar dates (HEVY_TIMEZONE, Europe/London by default). Ranges are inclusive at both ends.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

PRESETS = ("7d", "28d", "90d", "3m", "6m", "1y", "5y", "10y", "week", "month", "lastmonth", "year", "lastyear", "all", "custom")
MAX_CUSTOM_DAYS = 366 * 50


@dataclass(frozen=True)
class Period:
    key: str
    start: date
    end: date
    prev_start: date | None
    prev_end: date | None
    label: str

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def as_dict(self) -> dict:
        return {"key": self.key, "start": self.start.isoformat(), "end": self.end.isoformat(), "days": self.days,
                "label": self.label, "prev_start": self.prev_start.isoformat() if self.prev_start else None,
                "prev_end": self.prev_end.isoformat() if self.prev_end else None}


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _rolling(key: str, start: date, end: date, label: str) -> Period:
    days = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    return Period(key, start, end, prev_end - timedelta(days=days - 1), prev_end, label)


def resolve(key: str, today: date, first: date | None = None, start: date | None = None, end: date | None = None) -> Period:
    """Turn a preset (or custom start/end) into a Period with its comparison period."""
    if key in ("7d", "28d", "90d"):
        n = int(key[:-1])
        return _rolling(key, today - timedelta(days=n - 1), today, f"Last {n} days")
    if key in ("3m", "6m", "1y", "5y", "10y"):
        months = {"3m": 3, "6m": 6, "1y": 12, "5y": 60, "10y": 120}[key]
        label = {"3m": "Last 3 months", "6m": "Last 6 months", "1y": "Last 12 months", "5y": "Last 5 years", "10y": "Last 10 years"}[key]
        return _rolling(key, add_months(today, -months) + timedelta(days=1), today, label)
    if key == "lastmonth":
        start = add_months(today.replace(day=1), -1)
        end = today.replace(day=1) - timedelta(days=1)
        ps = add_months(start, -1)
        return Period(key, start, end, ps, start - timedelta(days=1), start.strftime("%B %Y"))
    if key == "lastyear":
        start, end = date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
        return Period(key, start, end, date(today.year - 2, 1, 1), date(today.year - 2, 12, 31), str(today.year - 1))
    if key == "week":
        start = today - timedelta(days=today.weekday())
        ps = start - timedelta(days=7)
        return Period(key, start, today, ps, ps + (today - start), "This week")
    if key == "month":
        start = today.replace(day=1)
        ps = add_months(start, -1)
        pe = min(ps + (today - start), start - timedelta(days=1))
        return Period(key, start, today, ps, pe, "This month")
    if key == "year":
        start = today.replace(month=1, day=1)
        ps = start.replace(year=start.year - 1)
        pe = min(ps + (today - start), start - timedelta(days=1))
        return Period(key, start, today, ps, pe, f"{today.year} so far")
    if key == "all":
        return Period(key, min(first or today, today), today, None, None, "All time")
    if key == "custom":
        if start is None or end is None or start > end or (end - start).days > MAX_CUSTOM_DAYS:
            raise ValueError("custom ranges need a start on or before the end")
        return _rolling(key, start, end, f"{start.isoformat()} to {end.isoformat()}")
    raise ValueError(f"unknown range {key!r}")
