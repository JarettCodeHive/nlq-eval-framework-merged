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


def assert_all_passed(results: list[IntegrityCheckResult]) -> None:
    """Raise a readable error if any integrity result failed."""

    failures = [result for result in results if not result.passed]
    if failures:
        messages = "; ".join(f"{item.check_name}: {item.message}" for item in failures)
        raise ValueError(f"Integrity validation failed: {messages}")
