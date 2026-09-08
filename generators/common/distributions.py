"""Shared statistical distributions for synthetic data generation."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from datetime import datetime
from datetime import timedelta
from typing import Any


def pareto_decimal_strings(
    rng: Any,
    count: int,
    alpha: float,
    min_amount: int,
    max_amount: int,
    scale: int,
) -> list[str]:
    """Generate bounded, fixed-scale amounts from a Pareto distribution.

    NumPy's Pareto sampler returns non-negative values. Adding one converts
    each sample into a classic Pareto multiplier whose minimum is one, and
    multiplying by ``min_amount`` moves the distribution's lower bound to the
    requested amount. Values above ``max_amount`` are clipped to that upper
    bound. Each bounded value is then rounded to ``scale`` decimal places and
    formatted as a string so CSV output does not inherit binary floating-point
    representation differences.

    A smaller ``alpha`` produces a heavier upper tail; a larger value keeps
    more observations near ``min_amount``. Clipping can cause multiple values
    to equal ``max_amount`` when samples extend beyond the configured bound.
    Results are deterministic when ``rng`` was created from a fixed seed.

    Args:
        rng: NumPy-compatible random generator used to draw Pareto samples.
        count: Number of amount strings to generate.
        alpha: Positive Pareto shape parameter.
        min_amount: Positive inclusive lower bound in major currency units.
        max_amount: Inclusive upper bound in major currency units.
        scale: Number of digits to retain after the decimal point.

    Returns:
        A list containing ``count`` bounded decimal strings, each formatted
        with exactly ``scale`` fractional digits.

    Raises:
        ValueError: If ``count`` or ``scale`` is negative, ``alpha`` or
            ``min_amount`` is not positive, or ``max_amount`` is less than
            ``min_amount``.
        ImportError: If NumPy is unavailable.
    """

    if count < 0:
        raise ValueError("count cannot be negative")
    if alpha <= 0:
        raise ValueError("alpha must be positive")
    if min_amount <= 0:
        raise ValueError("min_amount must be positive")
    if max_amount < min_amount:
        raise ValueError("max_amount must be greater than or equal to min_amount")
    if scale < 0:
        raise ValueError("scale cannot be negative")

    np = _require_numpy()
    raw_values = (rng.pareto(alpha, size=count) + 1) * min_amount
    clipped_values = np.clip(raw_values, min_amount, max_amount)
    multiplier = 10**scale
    cents = np.rint(clipped_values * multiplier).astype("int64")
    return [format_fixed_decimal(int(value), scale) for value in cents]


def gaussian_mixture_dates(
    rng: Any,
    count: int,
    start: date,
    end: date,
    component_centers: Sequence[float],
    component_weights: Sequence[float],
    std_fraction: float,
) -> list[str]:
    """Generate clustered ISO dates within an inclusive date range.

    The function delegates to :func:`gaussian_mixture_offsets`, which assigns
    each row to one Gaussian component and produces an integer offset from
    ``start``. Component centers are expressed as fractions of the full date
    span: for example, ``0.2`` places a center 20 percent of the way from
    ``start`` to ``end``. Generated offsets are clipped to the range, so no
    returned date precedes ``start`` or follows ``end``.

    Results use the ``YYYY-MM-DD`` ISO format and are deterministic when
    ``rng`` was created from a fixed seed.

    Args:
        rng: NumPy-compatible random generator used for component selection
            and Gaussian sampling.
        count: Number of date strings to generate.
        start: Inclusive lower date boundary.
        end: Inclusive upper date boundary.
        component_centers: Relative component locations within the date span.
        component_weights: Relative probabilities of selecting components.
        std_fraction: Gaussian standard deviation as a fraction of the full
            date span.

    Returns:
        A list containing ``count`` dates formatted as ``YYYY-MM-DD``.

    Raises:
        ValueError: If the date range or Gaussian-mixture configuration is
            invalid.
        ImportError: If NumPy is unavailable.
    """

    offsets = gaussian_mixture_offsets(
        rng,
        count=count,
        day_span=(end - start).days,
        component_centers=component_centers,
        component_weights=component_weights,
        std_fraction=std_fraction,
    )
    return [
        date.fromordinal(start.toordinal() + int(offset)).isoformat()
        for offset in offsets
    ]


def gaussian_mixture_timestamps(
    rng: Any,
    count: int,
    start: date,
    end: date,
    component_centers: Sequence[float],
    component_weights: Sequence[float],
    std_fraction: float,
) -> list[str]:
    """Generate clustered ISO timestamps within an inclusive date range.

    Gaussian-mixture sampling determines the date offset from ``start`` in the
    same way as :func:`gaussian_mixture_dates`. A separate uniformly sampled
    number of seconds supplies the time of day from ``00:00:00`` through
    ``23:59:59``. Consequently, an ``end`` boundary includes every time on the
    final date, not only midnight.

    Results use the ``YYYY-MM-DDTHH:MM:SS`` format and are deterministic when
    ``rng`` was created from a fixed seed.

    Args:
        rng: NumPy-compatible random generator used for component selection,
            Gaussian sampling, and time-of-day generation.
        count: Number of timestamp strings to generate.
        start: Inclusive lower date boundary.
        end: Inclusive upper date boundary.
        component_centers: Relative component locations within the date span.
        component_weights: Relative probabilities of selecting components.
        std_fraction: Gaussian standard deviation as a fraction of the full
            date span.

    Returns:
        A list containing ``count`` timestamps formatted as
        ``YYYY-MM-DDTHH:MM:SS``.

    Raises:
        ValueError: If the date range or Gaussian-mixture configuration is
            invalid.
        ImportError: If NumPy is unavailable.
    """

    offsets = gaussian_mixture_offsets(
        rng,
        count=count,
        day_span=(end - start).days,
        component_centers=component_centers,
        component_weights=component_weights,
        std_fraction=std_fraction,
    )
    seconds = rng.integers(0, 24 * 60 * 60, size=count)
    timestamps: list[str] = []
    for day_offset, second_offset in zip(offsets, seconds, strict=True):
        value = datetime.combine(
            date.fromordinal(start.toordinal() + int(day_offset)),
            datetime.min.time(),
        ) + timedelta(seconds=int(second_offset))
        timestamps.append(value.strftime("%Y-%m-%dT%H:%M:%S"))
    return timestamps


def gaussian_mixture_offsets(
    rng: Any,
    count: int,
    day_span: int,
    component_centers: Sequence[float],
    component_weights: Sequence[float],
    std_fraction: float,
) -> Any:
    """Generate bounded integer offsets from a Gaussian mixture.

    Relative component centers are multiplied by ``day_span`` to obtain their
    locations in days. Component weights are normalized to sum to one, and a
    component is selected independently for each requested value. Offsets are
    sampled around the selected centers with a standard deviation of
    ``day_span * std_fraction`` and a minimum spread of one day.

    Samples are rounded to the nearest integer and clipped to the inclusive
    interval ``[0, day_span]``. Clipping can place multiple observations at
    either boundary when a Gaussian tail extends outside the configured span.
    Results are deterministic when ``rng`` was created from a fixed seed.

    Args:
        rng: NumPy-compatible random generator used for component selection
            and Gaussian sampling.
        count: Number of integer offsets to generate.
        day_span: Maximum permitted offset from zero, in days.
        component_centers: Relative component locations, conventionally in
            the inclusive range zero through one.
        component_weights: Relative component-selection probabilities. The
            sequence must have the same length as ``component_centers``.
        std_fraction: Gaussian standard deviation as a fraction of
            ``day_span``; the implementation enforces a one-day minimum.

    Returns:
        A NumPy integer array of length ``count`` with values between zero and
        ``day_span``, inclusive.

    Raises:
        ValueError: If ``count`` or ``day_span`` is negative, no components are
            supplied, or center and weight counts differ. NumPy may also raise
            ``ValueError`` for unusable component weights.
        ImportError: If NumPy is unavailable.
    """

    if count < 0:
        raise ValueError("count cannot be negative")
    if day_span < 0:
        raise ValueError("day_span cannot be negative")
    if len(component_centers) != len(component_weights):
        raise ValueError("component centers and weights must have equal length")
    if not component_centers:
        raise ValueError("at least one component is required")

    np = _require_numpy()
    centers = np.array(component_centers, dtype=float) * day_span
    weights = np.array(component_weights, dtype=float)
    weights = weights / weights.sum()
    components = rng.choice(len(centers), size=count, p=weights)
    offsets = rng.normal(
        loc=centers[components],
        scale=max(1.0, day_span * std_fraction),
        size=count,
    )
    return np.clip(np.rint(offsets).astype(int), 0, day_span)


def poisson_weights(rng: Any, item_count: int, lam: float) -> Any:
    """Create positive Poisson-derived sampling weights."""

    if item_count <= 0:
        raise ValueError("item_count must be positive")
    if lam <= 0:
        raise ValueError("lambda must be positive")

    raw_weights = rng.poisson(lam=lam, size=item_count) + 1
    return raw_weights / raw_weights.sum()


def poisson_weighted_choices(
    rng: Any,
    values: Sequence[Any],
    count: int,
    lam: float,
) -> list[Any]:
    """Sample values using Poisson-derived weights."""

    if not values:
        raise ValueError("values must not be empty")
    if count < 0:
        raise ValueError("count cannot be negative")

    weights = poisson_weights(rng, len(values), lam=lam)
    return rng.choice(list(values), size=count, p=weights).tolist()


def format_fixed_decimal(value: int, scale: int) -> str:
    """Format an integer minor-unit value as a fixed-scale decimal string."""

    if scale == 0:
        return str(value)
    sign = "-" if value < 0 else ""
    absolute = abs(value)
    multiplier = 10**scale
    whole = absolute // multiplier
    fractional = absolute % multiplier
    return f"{sign}{whole}.{fractional:0{scale}d}"


def _require_numpy() -> Any:
    try:
        import numpy as np
    except ImportError as exc:
        raise ImportError(
            "Missing required dependency 'numpy'. Create the root .venv and "
            "install requirements.txt before applying distributions."
        ) from exc
    return np
