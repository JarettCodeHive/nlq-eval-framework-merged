"""Deterministic parameter sampling for question families."""

from __future__ import annotations

import itertools


def even_sample(values: list, k: int) -> list:
    """`k` items spread evenly across `values`, endpoints included."""
    n = len(values)
    if k >= n:
        return list(values)
    if k <= 1:
        return [values[0]]
    idx = sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})
    for j in range(n):  # fill any collision gaps
        if len(idx) >= k:
            break
        if j not in idx:
            idx = sorted(idx + [j])
    return [values[i] for i in idx[:k]]


def combos(param_values: dict[str, list]) -> list[dict]:
    """Cartesian product of any number of named parameters -> list of ctx dicts."""
    names = list(param_values)
    return [dict(zip(names, tup)) for tup in itertools.product(*(param_values[n] for n in names))]
