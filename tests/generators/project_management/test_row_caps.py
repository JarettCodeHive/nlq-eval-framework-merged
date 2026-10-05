from __future__ import annotations

import csv
from pathlib import Path

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.project_management.validators.row_caps import (
    ProjectManagementRowCapValidator,
)


EXPECTED_DEV_ROWS = {
    "projects": 10,
    "resources": 10,
    "tasks": 100,
    "task_resources": 300,
    "milestones": 25,
    "time_entries": 1515,
}

EXPECTED_FULL_ROWS = {
    "projects": 500,
    "resources": 100,
    "tasks": 10000,
    "task_resources": 30000,
    "milestones": 2500,
    "time_entries": 151500,
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
def test_expected_counts_include_duplicate_time_entries(
    profile: str,
    expected: dict[str, int],
) -> None:
    assert (
        ProjectManagementRowCapValidator.for_profile(
            profile
        ).expected_final_row_counts()
        == expected
    )


def test_expected_full_counts_pass_the_hard_cap() -> None:
    results = ProjectManagementRowCapValidator.for_profile(
        "full"
    ).validate_expected_counts()

    assert len(results) == 6
    assert all(result.passed for result in results)


def test_row_cap_failure_identifies_table_and_source() -> None:
    validator = ProjectManagementRowCapValidator.for_profile("full")
    counts = EXPECTED_FULL_ROWS | {
        "time_entries": validator.settings.max_rows_per_table + 1
    }
    checks = {
        result.check_name: result
        for result in validator._validate_counts(counts, source="actual")
    }

    assert not checks["time_entries.row_cap.actual"].passed
    assert (
        "250001 rows exceeds cap 250000"
        in checks["time_entries.row_cap.actual"].message
    )


def test_generated_dev_tables_pass_row_caps() -> None:
    results = ProjectManagementRowCapValidator.for_profile(
        "dev"
    ).generate_and_validate()

    assert len(results) == 6
    assert all(result.passed for result in results)
    assert all(result.check_name.endswith(".row_cap.actual") for result in results)


def test_exported_csv_directory_passes_row_caps(tmp_path: Path) -> None:
    validator = ProjectManagementRowCapValidator.for_profile("dev")
    for position, table_name in enumerate(validator.settings.table_order, start=1):
        _write_csv(tmp_path / f"{table_name}.csv", rows=position)

    results = validator.validate_csv_directory(tmp_path)

    assert len(results) == 6
    assert all(result.passed for result in results)
    assert all(result.check_name.endswith(".row_cap.exported") for result in results)


def test_exported_validation_reports_missing_csvs(tmp_path: Path) -> None:
    validator = ProjectManagementRowCapValidator.for_profile("full")

    with pytest.raises(FileNotFoundError, match="projects.csv"):
        validator.validate_csv_directory(tmp_path)


def test_generated_or_raise_fails_for_over_cap_table() -> None:
    validator = ProjectManagementRowCapValidator.for_profile("dev")
    tables = {
        table_name: range(
            validator.settings.max_rows_per_table + 1
            if table_name == "time_entries"
            else row_count
        )
        for table_name, row_count in EXPECTED_DEV_ROWS.items()
    }

    with pytest.raises(ValueError, match="time_entries.row_cap.actual"):
        validator.validate_generated_or_raise(tables)


def test_validator_rejects_non_pm_settings() -> None:
    settings = GenerationSettings.from_config_files("sales", "dev")
    with pytest.raises(ValueError, match="only supports project_management"):
        ProjectManagementRowCapValidator(DeterministicGenerator(settings))
