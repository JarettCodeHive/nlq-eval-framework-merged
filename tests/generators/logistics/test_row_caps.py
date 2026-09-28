from __future__ import annotations

import csv
from pathlib import Path

import pytest

from generators.logistics.validators.row_caps import LogisticsRowCapValidator


EXPECTED_DEV_ROWS = {
    "carriers": 10,
    "warehouses": 10,
    "orders": 1000,
    "shipments": 1515,
    "inventory": 500,
}

EXPECTED_FULL_ROWS = {
    "carriers": 50,
    "warehouses": 100,
    "orders": 100000,
    "shipments": 151500,
    "inventory": 50000,
}


def _write_csv(path: Path, rows: int) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["id", "notes"])
        for index in range(rows):
            writer.writerow([index + 1, "line one\nline two"])


@pytest.mark.parametrize(
    ("profile", "expected"),
    [("dev", EXPECTED_DEV_ROWS), ("full", EXPECTED_FULL_ROWS)],
)
def test_expected_counts_include_duplicate_shipments(
    profile: str,
    expected: dict[str, int],
) -> None:
    assert LogisticsRowCapValidator.for_profile(
        profile
    ).expected_final_row_counts() == expected


def test_expected_full_counts_pass_the_hard_cap() -> None:
    results = LogisticsRowCapValidator.for_profile(
        "full"
    ).validate_expected_counts()

    assert len(results) == 5
    assert all(result.passed for result in results)


def test_row_cap_failure_identifies_table_and_source() -> None:
    validator = LogisticsRowCapValidator.for_profile("full")
    counts = EXPECTED_FULL_ROWS | {
        "shipments": validator.settings.max_rows_per_table + 1
    }
    checks = {
        result.check_name: result
        for result in validator._validate_counts(counts, source="actual")
    }

    assert not checks["shipments.row_cap.actual"].passed
    assert "250001 rows exceeds cap 250000" in checks[
        "shipments.row_cap.actual"
    ].message


def test_generated_dev_tables_pass_row_caps() -> None:
    results = LogisticsRowCapValidator.for_profile("dev").generate_and_validate()

    assert len(results) == 5
    assert all(result.passed for result in results)
    assert all(result.check_name.endswith(".row_cap.actual") for result in results)


def test_exported_csv_directory_passes_row_caps(tmp_path: Path) -> None:
    validator = LogisticsRowCapValidator.for_profile("dev")
    for position, table_name in enumerate(validator.settings.table_order, start=1):
        _write_csv(tmp_path / f"{table_name}.csv", rows=position)

    results = validator.validate_csv_directory(tmp_path)

    assert len(results) == 5
    assert all(result.passed for result in results)
    assert all(result.check_name.endswith(".row_cap.exported") for result in results)


def test_exported_validation_reports_missing_csvs(tmp_path: Path) -> None:
    validator = LogisticsRowCapValidator.for_profile("full")

    with pytest.raises(FileNotFoundError, match="carriers.csv"):
        validator.validate_csv_directory(tmp_path)
