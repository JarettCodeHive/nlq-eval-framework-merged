"""Integrity checks for generated relational datasets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IntegrityCheckResult:
    """Result for one integrity check."""

    check_name: str
    passed: bool
    message: str


@dataclass(frozen=True)
class ReferenceValueClassification:
    """Counts and values for one nullable or orphanable relationship field."""

    empty_count: int
    valid_count: int
    declared_orphan_count: int
    unexpected_orphan_count: int
    declared_orphan_values: frozenset[str]
    unexpected_orphan_values: frozenset[str]


def passed(check_name: str, message: str) -> IntegrityCheckResult:
    """Create a passing integrity result."""

    return IntegrityCheckResult(check_name=check_name, passed=True, message=message)


def failed(check_name: str, message: str) -> IntegrityCheckResult:
    """Create a failing integrity result."""

    return IntegrityCheckResult(check_name=check_name, passed=False, message=message)


def non_empty_values(values: Any) -> set[str]:
    """Return non-empty stringified values from a pandas Series-like object."""

    return {str(value) for value in values.tolist() if str(value) != ""}


def empty_count(values: Any) -> int:
    """Return count of CSV-null empty string values."""

    return int((values.astype(str) == "").sum())


def classify_reference_values(
    values: Any,
    valid_parent_values: set[Any],
    declared_orphan_values: set[Any] | None = None,
) -> ReferenceValueClassification:
    """Classify relationship values without treating declared orphans as errors.

    Values are normalized to strings to match CSV-based integrity validation.
    Empty strings are counted separately. Any non-empty value outside both the
    valid parent set and the declared orphan set is an unexpected orphan.
    """

    valid = {str(value) for value in valid_parent_values}
    declared = {str(value) for value in declared_orphan_values or set()}
    overlap = valid & declared
    if overlap:
        raise ValueError(
            "declared orphan values overlap valid parent values: "
            f"{sorted(overlap)}"
        )

    empty = 0
    valid_count = 0
    declared_count = 0
    declared_found: set[str] = set()
    unexpected_found: set[str] = set()
    unexpected_count = 0
    for raw_value in values.tolist():
        value = str(raw_value)
        if value == "":
            empty += 1
        elif value in valid:
            valid_count += 1
        elif value in declared:
            declared_count += 1
            declared_found.add(value)
        else:
            unexpected_count += 1
            unexpected_found.add(value)

    return ReferenceValueClassification(
        empty_count=empty,
        valid_count=valid_count,
        declared_orphan_count=declared_count,
        unexpected_orphan_count=unexpected_count,
        declared_orphan_values=frozenset(declared_found),
        unexpected_orphan_values=frozenset(unexpected_found),
    )


def assert_all_passed(results: list[IntegrityCheckResult]) -> None:
    """Raise a readable error if any integrity result failed."""

    failures = [result for result in results if not result.passed]
    if failures:
        messages = "; ".join(f"{item.check_name}: {item.message}" for item in failures)
        raise ValueError(f"Integrity validation failed: {messages}")
