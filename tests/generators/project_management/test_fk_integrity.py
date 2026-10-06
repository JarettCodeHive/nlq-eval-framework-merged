from __future__ import annotations

from dataclasses import replace
import importlib.util
from pathlib import Path

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.core.csv_export import CSVExporter
from generators.project_management.imperfections import (
    ProjectManagementImperfectionInjector,
)
from generators.project_management.validators.fk_integrity import MANUAL_FK_CHECKS
from generators.project_management.validators.fk_integrity import (
    ProjectManagementDuckDBFKValidator,
)
from generators.project_management.validators.fk_integrity import (
    SEMANTIC_RELATIONSHIP_CHECKS,
)


pytestmark = pytest.mark.skipif(
    not all(
        importlib.util.find_spec(package) is not None
        for package in ("duckdb", "numpy", "pandas", "faker")
    ),
    reason="DuckDB and generation dependencies are not installed",
)


@pytest.fixture(scope="module")
def imperfect_tables() -> dict:
    return ProjectManagementImperfectionInjector.for_profile(
        "dev"
    ).generate_imperfect_tables()


def test_validator_uses_canonical_schema_and_expected_checks() -> None:
    validator = ProjectManagementDuckDBFKValidator.for_profile("full")

    assert validator.settings.schema_source.name == "project_management_ddl.sql"
    assert len(MANUAL_FK_CHECKS) == 6
    assert set(SEMANTIC_RELATIONSHIP_CHECKS) == {
        "time_entries.task_resource_membership"
    }
    assert "pragma foreign_key_check" not in " ".join(MANUAL_FK_CHECKS.values()).lower()


def test_generated_fk_validation_passes() -> None:
    results = ProjectManagementDuckDBFKValidator.for_profile(
        "dev"
    ).generate_and_validate()
    checks = _results_by_name(results)

    assert len(results) == 14
    assert all(result.passed for result in results)
    assert checks["schema.duckdb_ddl"].passed
    assert checks["time_entries.task_resource_membership"].passed


def test_constrained_load_rejects_orphan_fk(
    imperfect_tables: dict,
    tmp_path: Path,
) -> None:
    validator = ProjectManagementDuckDBFKValidator.for_profile("dev")
    malformed = _copy_tables(imperfect_tables)
    malformed["tasks"].loc[0, "project_id"] = 999999

    checks = _results_by_name(
        validator._validate_csv_paths(_export_tables(validator, malformed, tmp_path))
    )

    assert checks["schema.duckdb_ddl"].passed
    assert not checks["tasks.duckdb_load"].passed


def test_constrained_load_rejects_duplicate_composite_key(
    imperfect_tables: dict,
    tmp_path: Path,
) -> None:
    validator = ProjectManagementDuckDBFKValidator.for_profile("dev")
    malformed = _copy_tables(imperfect_tables)
    malformed["task_resources"].loc[1, ["task_id", "resource_id"]] = (
        malformed["task_resources"].loc[0, ["task_id", "resource_id"]].to_list()
    )

    checks = _results_by_name(
        validator._validate_csv_paths(_export_tables(validator, malformed, tmp_path))
    )

    assert not checks["task_resources.duckdb_load"].passed


def test_semantic_check_rejects_undeclared_assignment_pair(
    imperfect_tables: dict,
    tmp_path: Path,
) -> None:
    validator = ProjectManagementDuckDBFKValidator.for_profile("dev")
    malformed = _copy_tables(imperfect_tables)
    entry = malformed["time_entries"].iloc[0]
    assigned = set(
        malformed["task_resources"].loc[
            malformed["task_resources"]["task_id"] == entry["task_id"],
            "resource_id",
        ]
    )
    replacement = next(
        value
        for value in malformed["resources"]["resource_id"]
        if value not in assigned
    )
    malformed["time_entries"].loc[0, "resource_id"] = replacement

    checks = _results_by_name(
        validator._validate_csv_paths(_export_tables(validator, malformed, tmp_path))
    )

    assert checks["time_entries.duckdb_load"].passed
    assert not checks["time_entries.task_resource_membership"].passed


def test_invalid_ddl_is_reported(
    imperfect_tables: dict,
    tmp_path: Path,
) -> None:
    validator = ProjectManagementDuckDBFKValidator.for_profile("dev")
    ddl_path = tmp_path / "invalid.sql"
    ddl_path.write_text("CREATE TABLE broken (", encoding="utf-8")
    validator.settings = replace(validator.settings, schema_source=ddl_path)

    results = validator._validate_csv_paths(
        _export_tables(validator, imperfect_tables, tmp_path / "csv")
    )

    assert len(results) == 1
    assert results[0].check_name == "schema.duckdb_ddl"
    assert not results[0].passed


def test_missing_csvs_are_reported(tmp_path: Path) -> None:
    validator = ProjectManagementDuckDBFKValidator.for_profile("full")

    with pytest.raises(FileNotFoundError, match="projects.csv"):
        validator.validate_csv_directory(tmp_path)


def test_validator_rejects_non_pm_settings() -> None:
    settings = GenerationSettings.from_config_files("sales", "dev")
    with pytest.raises(ValueError, match="only supports project_management"):
        ProjectManagementDuckDBFKValidator(DeterministicGenerator(settings))


def _copy_tables(tables: dict) -> dict:
    return {name: table.copy(deep=True) for name, table in tables.items()}


def _export_tables(
    validator: ProjectManagementDuckDBFKValidator,
    tables: dict,
    output_dir: Path,
) -> dict[str, Path]:
    CSVExporter(validator.settings.csv_format).export_tables(
        tables=tables,
        table_order=validator.settings.table_order,
        output_dir=output_dir,
    )
    return validator._csv_paths(output_dir)


def _results_by_name(results: list) -> dict:
    return {result.check_name: result for result in results}
