"""Domain-neutral deterministic date and timestamp helpers."""

from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import timedelta
from typing import Any


def random_date_inclusive(rng: Any, start: date, end: date) -> date:
    """Draw one date from an inclusive range using the supplied RNG."""

    if end < start:
        raise ValueError("Random date range end cannot precede start")
    day_count = (end - start).days
    return start + timedelta(days=int(rng.integers(0, day_count + 1)))


def random_datetime_inclusive(
    rng: Any,
    start: datetime,
    end: datetime,
) -> datetime:
    """Draw one whole-second timestamp from an inclusive range."""

    if end < start:
        raise ValueError("Random timestamp range end cannot precede start")
    second_count = int((end - start).total_seconds())
    return start + timedelta(seconds=int(rng.integers(0, second_count + 1)))


def calendar_quarter_bounds(reference: date) -> tuple[date, date]:
    """Return inclusive quarter start and exclusive next-quarter start."""

    quarter_start_month = ((reference.month - 1) // 3) * 3 + 1
    quarter_start = date(reference.year, quarter_start_month, 1)
    if quarter_start_month == 10:
        return quarter_start, date(reference.year + 1, 1, 1)
    return quarter_start, date(reference.year, quarter_start_month + 3, 1)


__all__ = [
    "calendar_quarter_bounds",
    "random_date_inclusive",
    "random_datetime_inclusive",
]
