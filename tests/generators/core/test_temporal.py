from __future__ import annotations

from datetime import date
from datetime import datetime

import numpy as np
import pytest

from generators.core.temporal import calendar_quarter_bounds
from generators.core.temporal import random_date_inclusive
from generators.core.temporal import random_datetime_inclusive


def test_random_date_inclusive_is_seeded_and_bounded() -> None:
    first = random_date_inclusive(
        np.random.default_rng(42),
        date(2026, 1, 1),
        date(2026, 1, 31),
    )
    second = random_date_inclusive(
        np.random.default_rng(42),
        date(2026, 1, 1),
        date(2026, 1, 31),
    )

    assert first == second
    assert date(2026, 1, 1) <= first <= date(2026, 1, 31)
    assert random_date_inclusive(
        np.random.default_rng(1), date(2026, 1, 1), date(2026, 1, 1)
    ) == date(2026, 1, 1)


def test_random_datetime_inclusive_is_seeded_and_bounded() -> None:
    start = datetime(2026, 1, 1, 0, 0, 0)
    end = datetime(2026, 1, 1, 0, 1, 0)
    first = random_datetime_inclusive(np.random.default_rng(42), start, end)
    second = random_datetime_inclusive(np.random.default_rng(42), start, end)

    assert first == second
    assert start <= first <= end
    assert random_datetime_inclusive(np.random.default_rng(1), start, start) == start


def test_random_ranges_reject_reversed_bounds() -> None:
    rng = np.random.default_rng(42)
    with pytest.raises(ValueError, match="date range end cannot precede start"):
        random_date_inclusive(rng, date(2026, 1, 2), date(2026, 1, 1))
    with pytest.raises(ValueError, match="timestamp range end cannot precede start"):
        random_datetime_inclusive(
            rng,
            datetime(2026, 1, 2),
            datetime(2026, 1, 1),
        )


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        (date(2026, 1, 1), (date(2026, 1, 1), date(2026, 4, 1))),
        (date(2026, 8, 1), (date(2026, 7, 1), date(2026, 10, 1))),
        (date(2026, 12, 31), (date(2026, 10, 1), date(2027, 1, 1))),
    ],
)
def test_calendar_quarter_bounds_are_half_open(
    reference: date,
    expected: tuple[date, date],
) -> None:
    assert calendar_quarter_bounds(reference) == expected
