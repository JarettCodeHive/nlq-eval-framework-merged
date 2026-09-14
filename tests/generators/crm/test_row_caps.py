from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import pytest

from generators.crm.validators.row_caps import CRMRowCapValidator
from generators.crm.validators.row_caps import _csv_row_count
from main import build_parser


EXPECTED_FULL_ROWS = {
    "accounts": 24000,
    "contacts": 48480,
    "campaigns": 5000,
    "contact_campaigns": 180000,
    "interactions": 200000,
    "support_cases": 144000,
}


def _dependencies_available() -> bool:
    return all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    )


def _write_csv(path: Path, rows: int) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["id", "notes"])
        for index in range(rows):
            writer.writerow([index + 1, "line one\nline two"])


def test_expected_full_row_counts_include_duplicate_contacts() -> None:
    validator = CRMRowCapValidator.for_profile("full")

    assert validator.expected_final_row_counts() == EXPECTED_FULL_ROWS


def test_expected_row_caps_pass_for_full_profile() -> None:
    results = CRMRowCapValidator.for_profile("full").validate_expected_counts()

    assert len(results) == 6
    assert {result.check_name for result in results} == {
        f"{table_name}.row_cap.expected" for table_name in EXPECTED_FULL_ROWS
    }
    assert all(result.passed for result in results)


def test_row_cap_failure_identifies_table_over_limit() -> None:
    validator = CRMRowCapValidator.for_profile("full")
    row_counts = EXPECTED_FULL_ROWS | {
        "interactions": validator.settings.max_rows_per_table + 1
    }

    results = validator._validate_counts(row_counts, source="exported")
    checks = {result.check_name: result for result in results}

    assert not checks["interactions.row_cap.exported"].passed
    assert "exceeds cap 250000" in checks["interactions.row_cap.exported"].message


def test_csv_row_count_handles_quoted_multiline_fields(tmp_path: Path) -> None:
    path = tmp_path / "table.csv"
    _write_csv(path, rows=2)

    assert _csv_row_count(path) == 2


def test_csv_row_count_rejects_file_without_header(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    path.touch()

    with pytest.raises(ValueError, match="no header row"):
        _csv_row_count(path)


def test_exported_row_caps_read_all_engagement_csvs(tmp_path: Path) -> None:
    validator = CRMRowCapValidator.for_profile("dev")
    for position, table_name in enumerate(validator.settings.table_order, start=1):
        _write_csv(tmp_path / f"{table_name}.csv", rows=position)

    results = validator.validate_csv_directory(tmp_path)

    assert len(results) == 6
    assert all(result.passed for result in results)
    assert all(result.check_name.endswith(".row_cap.exported") for result in results)


def test_exported_row_caps_report_missing_csvs(tmp_path: Path) -> None:
    validator = CRMRowCapValidator.for_profile("full")

    with pytest.raises(FileNotFoundError, match="accounts.csv"):
        validator._csv_paths(tmp_path)


def test_cli_row_cap_sources_are_mutually_exclusive() -> None:
    parser = build_parser()

    args = parser.parse_args(["validate-row-caps", "--exported"])
    assert args.exported
    assert not args.generated

    with pytest.raises(SystemExit):
        parser.parse_args(["validate-row-caps", "--generated", "--exported"])


def test_generated_row_caps_when_dependencies_are_installed() -> None:
    if not _dependencies_available():
        pytest.skip("numpy, pandas, and Faker are not installed")

    results = CRMRowCapValidator.for_profile("dev").generate_and_validate()

    assert len(results) == 6
    assert all(result.passed for result in results)
