from __future__ import annotations

import pytest

from generators.core.fixed_point import allocate_integer_units


@pytest.mark.parametrize(
    ("total_units", "part_count", "expected"),
    [
        (10, 3, [4, 3, 3]),
        (-10, 3, [-4, -3, -3]),
        (2, 4, [1, 1, 0, 0]),
        (0, 3, [0, 0, 0]),
        (10000, 3, [3334, 3333, 3333]),
    ],
)
def test_allocate_integer_units_is_exact_and_deterministic(
    total_units: int,
    part_count: int,
    expected: list[int],
) -> None:
    result = allocate_integer_units(total_units, part_count)

    assert result == expected
    assert sum(result) == total_units


@pytest.mark.parametrize("part_count", [0, -1])
def test_allocate_integer_units_requires_positive_part_count(part_count: int) -> None:
    with pytest.raises(ValueError, match="part_count must be positive"):
        allocate_integer_units(10000, part_count)


@pytest.mark.parametrize(
    ("total_units", "part_count", "message"),
    [
        (1.0, 2, "total_units must be an integer"),
        (True, 2, "total_units must be an integer"),
        (10, 2.0, "part_count must be an integer"),
        (10, False, "part_count must be an integer"),
    ],
)
def test_allocate_integer_units_rejects_non_integer_inputs(
    total_units: object,
    part_count: object,
    message: str,
) -> None:
    with pytest.raises(TypeError, match=message):
        allocate_integer_units(total_units, part_count)  # type: ignore[arg-type]
