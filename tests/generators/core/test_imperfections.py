from __future__ import annotations

from generators.core.imperfections import count_from_pct
from generators.core.imperfections import inject_boundary_values
from generators.core.imperfections import inject_integer_outliers
from generators.core.imperfections import inject_nulls
from generators.core.imperfections import select_disjoint_positions


def test_count_from_pct() -> None:
    assert count_from_pct(0, 1.0) == 0
    assert count_from_pct(100, 1.0) == 1
    assert count_from_pct(101, 1.0) == 2
    assert count_from_pct(100, 0.0) == 0


def test_inject_boundary_values_when_pandas_is_installed() -> None:
    try:
        import pandas as pd
    except ImportError:
        return

    table = pd.DataFrame({"event_date": ["2026-01-01"] * 4})
    result = inject_boundary_values(
        table,
        "event_date",
        ["1900-01-01", "2038-01-19"],
        start_position=1,
    )

    assert result.loc[1, "event_date"] == "1900-01-01"
    assert result.loc[2, "event_date"] == "2038-01-19"


def test_inject_nulls_supports_integer_columns_when_dependencies_are_installed() -> (
    None
):
    try:
        import numpy as np
        import pandas as pd
    except ImportError:
        return

    table = pd.DataFrame({"account_id": [1, 2, 3, 4]})
    result = inject_nulls(
        table,
        "account_id",
        np.random.default_rng(42),
        null_pct=25.0,
    )

    assert (result["account_id"].astype(str) == "").sum() == 1
    assert table["account_id"].tolist() == [1, 2, 3, 4]


def test_inject_integer_outliers_when_dependencies_are_installed() -> None:
    try:
        import numpy as np
        import pandas as pd
    except ImportError:
        return

    table = pd.DataFrame({"points": [1] * 100})
    result = inject_integer_outliers(
        table,
        "points",
        np.random.default_rng(42),
        outlier_pct=5.0,
        min_outlier=50,
        max_outlier=100,
    )

    assert result["points"].between(50, 100).sum() == 5
    assert table["points"].tolist() == [1] * 100


def test_select_disjoint_positions_is_deterministic_and_non_overlapping() -> None:
    try:
        import numpy as np
    except ImportError:
        return

    first = select_disjoint_positions(
        np.random.default_rng(42),
        100,
        {"nulls": 2.5, "orphans": 1.0},
        protected_positions={0, 1},
    )
    second = select_disjoint_positions(
        np.random.default_rng(42),
        100,
        {"nulls": 2.5, "orphans": 1.0},
        protected_positions={0, 1},
    )

    assert first == second
    assert len(first["nulls"]) == 3
    assert len(first["orphans"]) == 1
    assert set(first["nulls"]).isdisjoint(first["orphans"])
    selected = set(first["nulls"]) | set(first["orphans"])
    assert not ({0, 1} & selected)


def test_select_disjoint_positions_rejects_insufficient_capacity() -> None:
    try:
        import numpy as np
    except ImportError:
        return

    try:
        select_disjoint_positions(
            np.random.default_rng(42),
            2,
            {"first": 50.0, "second": 50.0},
            protected_positions={0},
        )
    except ValueError as exc:
        assert "insufficient eligible positions" in str(exc)
    else:
        raise AssertionError("capacity violation was not rejected")
