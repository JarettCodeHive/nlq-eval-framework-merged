from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.core.csv_export import CSVExporter
from generators.project_management.config import load_project_management_config
from generators.project_management.imperfections import (
    ProjectManagementImperfectionInjector,
)
from generators.project_management.validators.join_paths import (
    MANY_TO_MANY_CARDINALITY_SQL,
)
from generators.project_management.validators.join_paths import (
    ProjectManagementJoinPathValidator,
)
from generators.project_management.validators.join_paths import REGISTERED_JOIN_SQL


EXPECTED_JOIN_PATH_IDS = [f"pm_jp_{number:03d}" for number in range(1, 11)]


pytestmark = pytest.mark.skipif(
    not all(
        importlib.util.find_spec(package) is not None
        for package in ("duckdb", "numpy", "pandas", "faker")
    ),
    reason="DuckDB and generation dependencies are not installed",
)


@pytest.fixture(scope="module")
def imperfect_tables() -> dict:
    return (
        ProjectManagementImperfectionInjector.for_profile("dev")
        .generate_imperfect_tables()
    )


def test_config_and_registered_sql_cover_required_paths() -> None:
    paths = load_project_management_config()["join_path_requirements"]

    assert [path["id"] for path in paths] == EXPECTED_JOIN_PATH_IDS
    assert paths[1]["required_result"] == "non_empty_with_unmatched_parent_rows"
    assert paths[9]["required_result"] == "complete_membership"
    assert set(REGISTERED_JOIN_SQL) == {"pm_jp_005", "pm_jp_009", "pm_jp_010"}


def test_many_to_many_sql_covers_both_bridge_directions() -> None:
    assert set(MANY_TO_MANY_CARDINALITY_SQL) == {
        "task_resources.task_many_resources",
        "task_resources.resource_many_tasks",
    }


def test_generated_join_path_validation_passes() -> None:
    results = ProjectManagementJoinPathValidator.for_profile(
        "dev"
    ).generate_and_validate()
    checks = _results_by_name(results)

    assert len(results) == 21
    assert all(result.passed for result in results)
    for join_id in EXPECTED_JOIN_PATH_IDS:
        assert checks[f"{join_id}.joined_rows"].passed
    assert checks["pm_jp_002.unmatched_parent_rows"].passed
    assert checks["pm_jp_010.invalid_memberships"].passed
    assert checks["task_resources.task_many_resources"].passed
    assert checks["task_resources.resource_many_tasks"].passed


def test_left_join_check_detects_absent_taskless_project(
    imperfect_tables: dict,
    tmp_path: Path,
) -> None:
    validator = ProjectManagementJoinPathValidator.for_profile("dev")
    modified = _copy_tables(imperfect_tables)
    taskless_id = next(
        project_id
        for project_id in modified["projects"]["project_id"]
        if project_id not in set(modified["tasks"]["project_id"])
    )
    modified["tasks"].loc[0, "project_id"] = taskless_id

    checks = _results_by_name(
        validator._validate_csv_paths(_export_tables(validator, modified, tmp_path))
    )

    assert not checks["pm_jp_002.unmatched_parent_rows"].passed


def test_complete_membership_detects_undeclared_pair(
    imperfect_tables: dict,
    tmp_path: Path,
) -> None:
    validator = ProjectManagementJoinPathValidator.for_profile("dev")
    modified = _copy_tables(imperfect_tables)
    entry = modified["time_entries"].iloc[0]
    assigned = set(
        modified["task_resources"].loc[
            modified["task_resources"]["task_id"] == entry["task_id"],
            "resource_id",
        ]
    )
    replacement = next(
        value
        for value in modified["resources"]["resource_id"]
        if value not in assigned
    )
    modified["time_entries"].loc[0, "resource_id"] = replacement

    checks = _results_by_name(
        validator._validate_csv_paths(_export_tables(validator, modified, tmp_path))
    )

    assert not checks["pm_jp_010.invalid_memberships"].passed


def test_many_to_many_check_detects_one_to_one_bridge() -> None:
    import duckdb

    with duckdb.connect(database=":memory:") as connection:
        connection.execute(
            "CREATE TABLE task_resources (task_id INTEGER, resource_id INTEGER)"
        )
        connection.execute("INSERT INTO task_resources VALUES (1, 1), (2, 2)")
        results = ProjectManagementJoinPathValidator.for_profile(
            "dev"
        )._validate_many_to_many_cardinality(connection)

    assert all(not result.passed for result in results)


def test_missing_csvs_are_reported(tmp_path: Path) -> None:
    validator = ProjectManagementJoinPathValidator.for_profile("full")

    with pytest.raises(FileNotFoundError, match="projects.csv"):
        validator.validate_csv_directory(tmp_path)


def test_validator_rejects_non_pm_settings() -> None:
    settings = GenerationSettings.from_config_files("sales", "dev")
    with pytest.raises(ValueError, match="only supports project_management"):
        ProjectManagementJoinPathValidator(DeterministicGenerator(settings))


def _copy_tables(tables: dict) -> dict:
    return {name: table.copy(deep=True) for name, table in tables.items()}


def _export_tables(
    validator: ProjectManagementJoinPathValidator,
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
