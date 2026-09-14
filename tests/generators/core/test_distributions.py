from __future__ import annotations

import importlib.util
from datetime import date

import pytest

from generators.core.distributions import format_fixed_decimal
from generators.core.distributions import gaussian_mixture_dates
from generators.core.distributions import gaussian_mixture_offsets
from generators.core.distributions import pareto_decimal_strings
from generators.core.distributions import poisson_weights


def test_format_fixed_decimal() -> None:
    assert format_fixed_decimal(123456, 2) == "1234.56"
    assert format_fixed_decimal(500000, 2) == "5000.00"
    assert format_fixed_decimal(-123456, 2) == "-1234.56"
    assert format_fixed_decimal(42, 0) == "42"


def test_pareto_distribution_when_numpy_is_installed() -> None:
    if importlib.util.find_spec("numpy") is None:
        pytest.skip("numpy is not installed")

    import numpy as np

    values = pareto_decimal_strings(
        np.random.default_rng(42),
        count=1000,
        alpha=1.16,
        min_amount=5000,
        max_amount=25000,
        scale=2,
    )

    assert len(values) == 1000
    assert all("." in value and len(value.rsplit(".", 1)[1]) == 2 for value in values)
    assert all(5000 <= float(value) <= 25000 for value in values)


def test_gaussian_mixture_dates_when_numpy_is_installed() -> None:
    if importlib.util.find_spec("numpy") is None:
        pytest.skip("numpy is not installed")

    import numpy as np

    values = gaussian_mixture_dates(
        np.random.default_rng(42),
        count=10,
        start=date(2023, 1, 1),
        end=date(2023, 12, 31),
        component_centers=(0.2, 0.55, 0.85),
        component_weights=(0.35, 0.35, 0.3),
        std_fraction=0.035,
    )

    assert len(values) == 10
    assert min(values) >= "2023-01-01"
    assert max(values) <= "2023-12-31"


def test_gaussian_mixture_offsets_follow_component_weights() -> None:
    if importlib.util.find_spec("numpy") is None:
        pytest.skip("numpy is not installed")

    import numpy as np

    offsets = gaussian_mixture_offsets(
        np.random.default_rng(42),
        count=10_000,
        day_span=100,
        component_centers=(0.2, 0.8),
        component_weights=(0.7, 0.3),
        std_fraction=0.01,
    )

    lower_cluster_fraction = float((offsets < 50).mean())
    assert lower_cluster_fraction == pytest.approx(0.7, abs=0.02)
    assert int(offsets.min()) >= 0
    assert int(offsets.max()) <= 100


def test_poisson_weights_are_positive_normalized_and_non_uniform() -> None:
    if importlib.util.find_spec("numpy") is None:
        pytest.skip("numpy is not installed")

    import numpy as np

    weights = poisson_weights(np.random.default_rng(42), item_count=100, lam=3.2)

    assert len(weights) == 100
    assert float(weights.sum()) == pytest.approx(1.0)
    assert bool((weights > 0).all())
    assert len(set(weights.tolist())) > 1
