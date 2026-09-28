from __future__ import annotations

import pytest

from generators.core.integrity import classify_reference_values


def test_classify_reference_values_separates_declared_orphans() -> None:
    pd = pytest.importorskip("pandas")
    values = pd.Series([1, 2, "", 900000004, 800000005, 900000004])

    result = classify_reference_values(
        values,
        valid_parent_values={1, 2, 3},
        declared_orphan_values={900000004},
    )

    assert result.empty_count == 1
    assert result.valid_count == 2
    assert result.declared_orphan_count == 2
    assert result.unexpected_orphan_count == 1
    assert result.declared_orphan_values == frozenset({"900000004"})
    assert result.unexpected_orphan_values == frozenset({"800000005"})


def test_classify_reference_values_rejects_parent_orphan_overlap() -> None:
    pd = pytest.importorskip("pandas")

    with pytest.raises(ValueError, match="overlap valid parent"):
        classify_reference_values(
            pd.Series([1]),
            valid_parent_values={1},
            declared_orphan_values={1},
        )
