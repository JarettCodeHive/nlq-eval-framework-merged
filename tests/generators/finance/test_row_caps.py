from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.core.csv_export import count_csv_rows
from generators.finance.validators.row_caps import FinanceRowCapValidator


EXPECTED_DEV_ROWS = {
    "accounts": 50,
    "transactions": 1000,
    "ledger_entries": 2000,
    "budgets": 101,
    "fx_rates": 370,
}

EXPECTED_FULL_ROWS = {
    "accounts": 200,
    "transactions": 100000,
    "ledger_entries": 200000,
    "budgets": 808,
    "fx_rates": 3650,
}


def _dependencies_available() -> bool:
    return all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    )


def _write_csv(path: Path, rows: int) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["id", "description"])
        for index in range(rows):
            writer.writerow([index + 1, "line one\nline two"])


@pytest.mark.parametrize(
    ("profile", "expected"),
    [("dev", EXPECTED_DEV_ROWS), ("full", EXPECTED_FULL_ROWS)],
)
def test_expected_counts_include_duplicate_budgets(
    profile: str,
    expected: dict[str, int],
) -> None:
    assert (
        FinanceRowCapValidator.for_profile(profile).expected_final_row_counts()
        == expected
    )


def test_expected_full_counts_pass_the_hard_cap() -> None:
    results = FinanceRowCapValidator.for_profile("full").validate_expected_counts()

    assert len(results) == 5
    assert all(result.passed for result in results)
    assert {result.check_name for result in results} == {
        f"{table_name}.row_cap.expected" for table_name in EXPECTED_FULL_ROWS
    }


def test_row_cap_failure_identifies_the_table_and_source() -> None:
    validator = FinanceRowCapValidator.for_profile("full")
    counts = EXPECTED_FULL_ROWS | {
        "ledger_entries": validator.settings.max_rows_per_table + 1
    }
    checks = {
        result.check_name: result
        for result in validator._validate_counts(counts, source="actual")
    }

    assert not checks["ledger_entries.row_cap.actual"].passed
    assert "250001 rows exceeds cap 250000" in checks[
        "ledger_entries.row_cap.actual"
    ].message


def test_generated_dev_tables_pass_row_caps() -> None:
    if not _dependencies_available():
        pytest.skip("numpy, pandas, and Faker are not installed")

    results = FinanceRowCapValidator.for_profile("dev").generate_and_validate()

    assert len(results) == 5
    assert all(result.passed for result in results)
    assert all(result.check_name.endswith(".row_cap.actual") for result in results)


def test_csv_counter_handles_quoted_multiline_records(tmp_path: Path) -> None:
    path = tmp_path / "table.csv"
    _write_csv(path, rows=2)

    assert count_csv_rows(path) == 2


def test_exported_csv_directory_passes_row_caps(tmp_path: Path) -> None:
    validator = FinanceRowCapValidator.for_profile("dev")
    for position, table_name in enumerate(validator.settings.table_order, start=1):
        _write_csv(tmp_path / f"{table_name}.csv", rows=position)

    results = validator.validate_csv_directory(tmp_path)

    assert len(results) == 5
    assert all(result.passed for result in results)
    assert all(result.check_name.endswith(".row_cap.exported") for result in results)


def test_exported_validation_reports_all_missing_csvs(tmp_path: Path) -> None:
    validator = FinanceRowCapValidator.for_profile("full")

    with pytest.raises(FileNotFoundError, match="accounts.csv"):
        validator.validate_csv_directory(tmp_path)


def test_generated_or_raise_fails_for_over_cap_table() -> None:
    validator = FinanceRowCapValidator.for_profile("dev")
    tables = {
        table_name: range(
            validator.settings.max_rows_per_table + 1
            if table_name == "ledger_entries"
            else row_count
        )
        for table_name, row_count in EXPECTED_DEV_ROWS.items()
    }

    with pytest.raises(ValueError, match="ledger_entries.row_cap.actual"):
        validator.validate_generated_or_raise(tables)


def test_validator_rejects_non_finance_settings() -> None:
    settings = GenerationSettings.from_config_files("sales", "dev")
    with pytest.raises(ValueError, match="only supports finance"):
        FinanceRowCapValidator(DeterministicGenerator(settings))
