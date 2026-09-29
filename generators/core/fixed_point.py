"""Domain-neutral helpers for exact fixed-point unit allocation."""

from __future__ import annotations


def allocate_integer_units(total_units: int, part_count: int) -> list[int]:
    """Split signed integer units exactly and deterministically.

    The first ``abs(total_units) % part_count`` parts receive one additional
    unit. Parts differ by at most one unit and always sum to ``total_units``.
    Callers choose what one unit represents, such as one cent, one basis point,
    or one ten-thousandth of a currency unit.
    """

    if not isinstance(total_units, int) or isinstance(total_units, bool):
        raise TypeError("total_units must be an integer")
    if not isinstance(part_count, int) or isinstance(part_count, bool):
        raise TypeError("part_count must be an integer")
    if part_count <= 0:
        raise ValueError("part_count must be positive")

    sign = -1 if total_units < 0 else 1
    quotient, remainder = divmod(abs(total_units), part_count)
    return [
        sign * (quotient + (1 if position < remainder else 0))
        for position in range(part_count)
    ]


__all__ = ["allocate_integer_units"]
