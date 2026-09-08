"""Controlled imperfection helpers for synthetic datasets."""

from __future__ import annotations

import math
from typing import Any

from generators.common.distributions import format_fixed_decimal


def count_from_pct(total: int, pct: float, minimum: int = 1) -> int:
    """Return a deterministic non-zero count for a percentage when rows exist."""

    if total <= 0 or pct <= 0:
        return 0
    return max(minimum, math.ceil(total * pct / 100))


def inject_nulls(
    table: Any,
    column_name: str,
    rng: Any,
    null_pct: float,
    protected_positions: set[int] | None = None,
) -> Any:
    """Set a percentage of nullable column values to empty CSV fields."""

    protected_positions = protected_positions or set()
    candidate_positions = [
        position
        for position in range(len(table))
        if position not in protected_positions
    ]
    null_count = min(count_from_pct(len(table), null_pct), len(candidate_positions))
    result = table.copy(deep=True)
    if null_count == 0:
        return result

    result[column_name] = result[column_name].astype("object")
    selected_positions = rng.choice(candidate_positions, size=null_count, replace=False)
    result.loc[selected_positions, column_name] = ""
    return result


def inject_fixed_scale_outliers(
    table: Any,
    column_name: str,
    rng: Any,
    outlier_pct: float,
    min_outlier: int,
    max_outlier: int,
    scale: int,
) -> Any:
    """Replace selected numeric values with fixed-scale outlier strings."""

    outlier_count = count_from_pct(len(table), outlier_pct)
    result = table.copy(deep=True)
    if outlier_count == 0:
        return result

    selected_positions = rng.choice(
        table.index.to_numpy(),
        size=outlier_count,
        replace=False,
    )
    multiplier = 10**scale
    outlier_values = rng.integers(
        min_outlier * multiplier,
        (max_outlier * multiplier) + 1,
        size=outlier_count,
    )
    result.loc[selected_positions, column_name] = [
        format_fixed_decimal(int(value), scale) for value in outlier_values
    ]
    return result


def inject_integer_outliers(
    table: Any,
    column_name: str,
    rng: Any,
    outlier_pct: float,
    min_outlier: int,
    max_outlier: int,
) -> Any:
    """Replace selected values with integers from an inclusive outlier range."""

    if min_outlier > max_outlier:
        raise ValueError("min_outlier cannot exceed max_outlier")
    outlier_count = count_from_pct(len(table), outlier_pct)
    result = table.copy(deep=True)
    if outlier_count == 0:
        return result

    selected_positions = rng.choice(
        table.index.to_numpy(),
        size=outlier_count,
        replace=False,
    )
    result.loc[selected_positions, column_name] = rng.integers(
        min_outlier,
        max_outlier + 1,
        size=outlier_count,
    )
    return result


def inject_boundary_values(
    table: Any,
    column_name: str,
    values: list[str],
    start_position: int = 0,
) -> Any:
    """Inject deterministic boundary values at known row positions."""

    result = table.copy(deep=True)
    if not values or len(result) == 0:
        return result
    for offset, value in enumerate(values):
        position = (start_position + offset) % len(result)
        result.loc[position, column_name] = value
    return result
