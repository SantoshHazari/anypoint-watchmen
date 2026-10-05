"""Time-window helpers for Anypoint Usage API."""

from __future__ import annotations

import datetime as dt


def utc_ms(value: dt.datetime) -> int:
    return int(value.timestamp() * 1000)


def default_closed_window(days: int) -> tuple[dt.datetime, dt.datetime]:
    """Return (start, end) spanning `days` up to today (inclusive).

    The Usage API may return provisional data for today/yesterday, but
    omitting recent days causes gaps for low-traffic orgs. We include
    today so all available data is captured.
    """
    now = dt.datetime.now(dt.timezone.utc)
    end_date = now.date()
    end = dt.datetime.combine(end_date, dt.time(23, 59, 59), tzinfo=dt.timezone.utc)
    start = dt.datetime.combine(end_date - dt.timedelta(days=days - 1), dt.time.min, tzinfo=dt.timezone.utc)
    return start, end


def parse_date_window(start_date: str, end_date: str) -> tuple[dt.datetime, dt.datetime]:
    start = dt.datetime.fromisoformat(start_date).replace(tzinfo=dt.timezone.utc)
    end = dt.datetime.fromisoformat(end_date).replace(tzinfo=dt.timezone.utc)
    if len(start_date) == 10:
        start = dt.datetime.combine(start.date(), dt.time(0, 0, 0), tzinfo=dt.timezone.utc)
    if len(end_date) == 10:
        end = dt.datetime.combine(end.date(), dt.time(23, 59, 59), tzinfo=dt.timezone.utc)
    return start, end
