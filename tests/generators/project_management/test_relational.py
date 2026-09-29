from __future__ import annotations

from datetime import date
from datetime import timedelta
import importlib.util

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.project_management.generator import (
    PROJECT_MANAGEMENT_COLUMN_CONTRACTS,
)
from generators.project_management.imperfections import (
    ProjectManagementImperfectionInjector,
)
from generators.project_management.validators.relational import (
    ProjectManagementRelationalValidator,
)


pytestmark = pytest.mark.skipif(
    not all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    ),
    reason="numpy, pandas, and Faker are not installed",
)


@pytest.fixture(scope="module")
def imperfect_tables() -> dict:
    return (
        ProjectManagementImperfectionInjector.for_profile("dev")
        .generate_imperfect_tables()
    )


def test_generated_relational_validation_passes() -> None:
    validator = ProjectManagementRelationalValidator.for_profile("dev")
    results = validator.generate_and_validate()
    checks = _results_by_name(results)

    assert validator.settings.table_order == tuple(
        PROJECT_MANAGEMENT_COLUMN_CONTRACTS
    )
    assert results and all(result.passed for result in results)
    assert "tasks.project_id.fk" in checks
    assert "task_resources.unique_pairs" in checks
    assert "task_resources.many_to_many" in checks
    assert "time_entries.assignment_membership" in checks
    assert "tasks.status_date_consistency" in checks
    assert "milestones.status_date_consistency" in checks
    assert "projects.without_tasks" in checks
    assert "project_task_time_entry.inner_path" in checks


def test_missing_table_reports_contract_failure_without_crashing(
    imperfect_tables: dict,
) -> None:
    malformed = _copy_tables(imperfect_tables)
    malformed.pop("milestones")
    checks = _results_by_name(
        ProjectManagementRelationalValidator.for_profile("dev").validate_tables(
            malformed
        )
    )

    assert not checks["schema.table_order"].passed
    assert not checks["milestones.present"].passed


def test_wrong_column_order_stops_dependent_checks(imperfect_tables: dict) -> None:
    malformed = _copy_tables(imperfect_tables)
    columns = malformed["tasks"].columns.tolist()
    columns[0], columns[1] = columns[1], columns[0]
    malformed["tasks"] = malformed["tasks"][columns]
    checks = _results_by_name(
        ProjectManagementRelationalValidator.for_profile("dev").validate_tables(
            malformed
        )
    )

    assert not checks["tasks.columns"].passed
    assert "tasks.row_count" not in checks


def test_key_fk_required_and_row_count_failures_are_reported(
    imperfect_tables: dict,
) -> None:
    malformed = _copy_tables(imperfect_tables)
    malformed["time_entries"].loc[1, "entry_id"] = malformed["time_entries"].loc[
        0, "entry_id"
    ]
    malformed["tasks"].loc[0, "project_id"] = 999999
    malformed["projects"].loc[0, "project_name"] = ""
    malformed["milestones"] = malformed["milestones"].iloc[:-1]
    checks = _results_by_name(
        ProjectManagementRelationalValidator.for_profile("dev").validate_tables(
            malformed
        )
    )

    assert not checks["time_entries.primary_key"].passed
    assert not checks["tasks.project_id.fk"].passed
    assert not checks["projects.project_name.not_null"].passed
    assert not checks["milestones.row_count"].passed


def test_assignment_membership_and_allocation_failures_are_reported(
    imperfect_tables: dict,
) -> None:
    malformed = _copy_tables(imperfect_tables)
    entry = malformed["time_entries"].iloc[0]
    assigned_resources = set(
        malformed["task_resources"].loc[
            malformed["task_resources"]["task_id"] == entry["task_id"],
            "resource_id",
        ]
    )
    replacement = next(
        resource_id
        for resource_id in malformed["resources"]["resource_id"]
        if resource_id not in assigned_resources
    )
    malformed["time_entries"].loc[0, "resource_id"] = replacement
    malformed["task_resources"].loc[0, "allocation_pct"] = "99.99"
    checks = _results_by_name(
        ProjectManagementRelationalValidator.for_profile("dev").validate_tables(
            malformed
        )
    )

    assert not checks["time_entries.assignment_membership"].passed
    assert not checks["task_resources.allocation_total"].passed


def test_project_task_and_status_date_failures_are_reported(
    imperfect_tables: dict,
) -> None:
    malformed = _copy_tables(imperfect_tables)
    project_position = malformed["projects"].index[
        malformed["projects"]["end_date"] != ""
    ][0]
    project_start = date.fromisoformat(
        malformed["projects"].loc[project_position, "start_date"]
    )
    malformed["projects"].loc[project_position, "end_date"] = (
        project_start - timedelta(days=1)
    ).isoformat()
    task_position = malformed["tasks"].index[
        malformed["tasks"]["status"] == "Completed"
    ][0]
    malformed["tasks"].loc[task_position, "completed_date"] = ""
    checks = _results_by_name(
        ProjectManagementRelationalValidator.for_profile("dev").validate_tables(
            malformed
        )
    )

    assert not checks["projects.chronology"].passed
    assert not checks["tasks.parent_project_window"].passed
    assert not checks["tasks.status_date_consistency"].passed


def test_milestone_assignment_and_entry_date_failures_are_reported(
    imperfect_tables: dict,
) -> None:
    malformed = _copy_tables(imperfect_tables)
    milestone_position = malformed["milestones"].index[
        malformed["milestones"]["status"] == "Completed"
    ][0]
    malformed["milestones"].loc[milestone_position, "actual_date"] = ""
    assignment_position = malformed["task_resources"].index[
        malformed["task_resources"]["released_at"] != ""
    ][0]
    malformed["task_resources"].loc[
        assignment_position, "released_at"
    ] = "1900-01-01"
    malformed["time_entries"].loc[0, "created_at"] = "1900-01-01T00:00:00"
    checks = _results_by_name(
        ProjectManagementRelationalValidator.for_profile("dev").validate_tables(
            malformed
        )
    )

    assert not checks["milestones.status_date_consistency"].passed
    assert not checks["task_resources.chronology"].passed
    assert not checks["time_entries.assignment_window"].passed


def test_decimal_domain_and_currency_failures_are_reported(
    imperfect_tables: dict,
) -> None:
    malformed = _copy_tables(imperfect_tables)
    malformed["projects"].loc[0, "budget_amount"] = "-1.0"
    malformed["tasks"].loc[0, "status"] = "Unknown"
    malformed["resources"].loc[0, "currency_code"] = "EUR"
    checks = _results_by_name(
        ProjectManagementRelationalValidator.for_profile("dev").validate_tables(
            malformed
        )
    )

    assert not checks["projects.budget_amount.fixed_decimal"].passed
    assert not checks["tasks.status.domain"].passed
    assert not checks["resources.currency_code"].passed


def test_left_join_and_open_range_failures_are_reported(
    imperfect_tables: dict,
) -> None:
    malformed = _copy_tables(imperfect_tables)
    taskless_id = next(
        project_id
        for project_id in malformed["projects"]["project_id"]
        if project_id not in set(malformed["tasks"]["project_id"])
    )
    source_project = malformed["tasks"]["project_id"].value_counts().index[0]
    position = malformed["tasks"].index[
        malformed["tasks"]["project_id"] == source_project
    ][0]
    malformed["tasks"].loc[position, "project_id"] = taskless_id
    malformed["projects"].loc[
        malformed["projects"]["end_date"] == "", "end_date"
    ] = malformed["projects"]["start_date"]
    malformed["tasks"].loc[
        malformed["tasks"]["due_date"] == "", "due_date"
    ] = malformed["tasks"]["start_date"]
    checks = _results_by_name(
        ProjectManagementRelationalValidator.for_profile("dev").validate_tables(
            malformed
        )
    )

    assert not checks["projects.without_tasks"].passed
    assert not checks["projects.open_ended"].passed
    assert not checks["tasks.open_ended"].passed


def test_validate_or_raise_combines_failures(imperfect_tables: dict) -> None:
    malformed = _copy_tables(imperfect_tables)
    malformed["projects"].loc[0, "project_name"] = ""
    malformed["resources"].loc[0, "currency_code"] = "EUR"

    with pytest.raises(ValueError, match="Integrity validation failed"):
        ProjectManagementRelationalValidator.for_profile("dev").validate_or_raise(
            malformed
        )


def test_validator_rejects_non_pm_settings() -> None:
    settings = GenerationSettings.from_config_files("sales", "dev")
    with pytest.raises(ValueError, match="only supports project_management"):
        ProjectManagementRelationalValidator(DeterministicGenerator(settings))


def _copy_tables(tables: dict) -> dict:
    return {name: table.copy(deep=True) for name, table in tables.items()}


def _results_by_name(results: list) -> dict:
    return {result.check_name: result for result in results}
