from __future__ import annotations

import importlib.util

import pytest

import generators.project_management.generator as generator_module
from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.project_management.generator import (
    ProjectManagementBaseEntityGenerator,
)
from generators.project_management.validators.generated_tables import (
    ProjectManagementGeneratedTablesValidator,
)


pytestmark = pytest.mark.skipif(
    not all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    ),
    reason="numpy, pandas, and Faker are required",
)


@pytest.fixture(scope="module")
def valid_generation() -> tuple[
    ProjectManagementBaseEntityGenerator,
    dict[str, object],
]:
    generator = ProjectManagementBaseEntityGenerator.for_profile("dev")
    return generator, generator.generate_tables()


def _copy_tables(tables: dict[str, object]) -> dict[str, object]:
    return {
        table_name: table.copy(deep=True)  # type: ignore[attr-defined]
        for table_name, table in tables.items()
    }


def test_immediate_validator_accepts_clean_base_tables(
    valid_generation: tuple,
) -> None:
    generator, tables = valid_generation

    ProjectManagementGeneratedTablesValidator(generator.generator).validate(tables)


def test_generator_invokes_immediate_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, object]] = []

    def validate(context: object, tables: object) -> None:
        calls.append((context, tables))

    monkeypatch.setattr(
        generator_module,
        "validate_project_management_generated_tables",
        validate,
    )
    generator = ProjectManagementBaseEntityGenerator.for_profile("dev")
    tables = generator.generate_tables()

    assert calls == [(generator.generator, tables)]


def test_guard_rejects_non_pm_settings() -> None:
    context = DeterministicGenerator(
        GenerationSettings.from_config_files("sales", "dev")
    )

    with pytest.raises(ValueError, match="only supports the project_management"):
        ProjectManagementGeneratedTablesValidator(context)


def test_guard_rejects_table_column_and_row_drift(valid_generation: tuple) -> None:
    generator, tables = valid_generation
    reordered = _copy_tables(tables)
    reordered = {"tasks": reordered.pop("tasks"), **reordered}
    with pytest.raises(ValueError, match="table order differs"):
        ProjectManagementGeneratedTablesValidator(generator.generator).validate(
            reordered
        )

    missing_column = _copy_tables(tables)
    missing_column["resources"] = missing_column["resources"].drop(  # type: ignore[attr-defined]
        columns=["department"]
    )
    with pytest.raises(ValueError, match="resources generated columns differ"):
        ProjectManagementGeneratedTablesValidator(generator.generator).validate(
            missing_column
        )

    missing_row = _copy_tables(tables)
    missing_row["milestones"] = missing_row["milestones"].iloc[:-1].copy()  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="milestones generated row count differs"):
        ProjectManagementGeneratedTablesValidator(generator.generator).validate(
            missing_row
        )


def test_guard_rejects_primary_and_required_field_corruption(
    valid_generation: tuple,
) -> None:
    generator, tables = valid_generation
    duplicate_key = _copy_tables(tables)
    duplicate_key["task_resources"].loc[1, ["task_id", "resource_id"]] = (  # type: ignore[attr-defined]
        duplicate_key["task_resources"].loc[0, ["task_id", "resource_id"]]  # type: ignore[attr-defined]
    )
    with pytest.raises(ValueError, match="task_resources contains duplicate"):
        ProjectManagementGeneratedTablesValidator(generator.generator).validate(
            duplicate_key
        )

    blank_required = _copy_tables(tables)
    blank_required["projects"].loc[0, "project_name"] = ""  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="project_name.*blank required"):
        ProjectManagementGeneratedTablesValidator(generator.generator).validate(
            blank_required
        )


def test_guard_rejects_physical_and_semantic_orphans(
    valid_generation: tuple,
) -> None:
    generator, tables = valid_generation
    physical = _copy_tables(tables)
    physical["milestones"].loc[0, "project_id"] = 999999  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="milestones.project_id contains orphan"):
        ProjectManagementGeneratedTablesValidator(generator.generator).validate(
            physical
        )

    semantic = _copy_tables(tables)
    entry = semantic["time_entries"].iloc[0]  # type: ignore[attr-defined]
    assignments = set(
        semantic["task_resources"][["task_id", "resource_id"]].itertuples(  # type: ignore[index]
            index=False, name=None
        )
    )
    replacement = next(
        resource_id
        for resource_id in semantic["resources"]["resource_id"]  # type: ignore[index]
        if (entry.task_id, resource_id) not in assignments
    )
    semantic["time_entries"].loc[0, "resource_id"] = replacement  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="undeclared task/resource pairs"):
        ProjectManagementGeneratedTablesValidator(generator.generator).validate(
            semantic
        )


def test_guard_rejects_domain_and_decimal_corruption(valid_generation: tuple) -> None:
    generator, tables = valid_generation
    domain = _copy_tables(tables)
    domain["tasks"].loc[0, "task_type"] = "Unknown"  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="task_type contains unknown"):
        ProjectManagementGeneratedTablesValidator(generator.generator).validate(domain)

    decimal_scale = _copy_tables(tables)
    decimal_scale["time_entries"].loc[0, "hours"] = "1.0"  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="hours must use fixed scale 2"):
        ProjectManagementGeneratedTablesValidator(generator.generator).validate(
            decimal_scale
        )


def test_guard_rejects_task_and_milestone_status_date_corruption(
    valid_generation: tuple,
) -> None:
    generator, tables = valid_generation
    task = _copy_tables(tables)
    completed_position = task["tasks"].index[  # type: ignore[attr-defined]
        task["tasks"]["status"] == "Completed"  # type: ignore[index]
    ][0]
    task["tasks"].loc[completed_position, "completed_date"] = ""  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="Completed task lacks"):
        ProjectManagementGeneratedTablesValidator(generator.generator).validate(task)

    milestone = _copy_tables(tables)
    open_position = milestone["milestones"].index[  # type: ignore[attr-defined]
        milestone["milestones"]["status"] != "Completed"  # type: ignore[index]
    ][0]
    milestone["milestones"].loc[open_position, "actual_date"] = "2026-01-01"  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="Incomplete milestone unexpectedly"):
        ProjectManagementGeneratedTablesValidator(generator.generator).validate(
            milestone
        )


def test_guard_rejects_assignment_allocation_and_time_window_corruption(
    valid_generation: tuple,
) -> None:
    generator, tables = valid_generation
    allocation = _copy_tables(tables)
    allocation["task_resources"].loc[0, "allocation_pct"] = "1.00"  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="allocations do not sum"):
        ProjectManagementGeneratedTablesValidator(generator.generator).validate(
            allocation
        )

    entry = _copy_tables(tables)
    entry["time_entries"].loc[0, "entry_date"] = "1901-01-01"  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="outside the assignment window"):
        ProjectManagementGeneratedTablesValidator(generator.generator).validate(entry)


def test_guard_rejects_missing_join_cases(valid_generation: tuple) -> None:
    generator, tables = valid_generation
    validator = ProjectManagementGeneratedTablesValidator(generator.generator)
    no_unmatched = _copy_tables(tables)
    project_ids = set(no_unmatched["projects"]["project_id"])  # type: ignore[index]
    represented = set(no_unmatched["tasks"]["project_id"])  # type: ignore[index]
    taskless_id = next(iter(project_ids - represented))
    no_unmatched["tasks"].loc[0, "project_id"] = taskless_id  # type: ignore[attr-defined]

    with pytest.raises(ValueError, match="lacks a project without tasks"):
        validator._validate_required_join_cases(no_unmatched)

    no_many_to_many = _copy_tables(tables)
    assignments = no_many_to_many["task_resources"]
    for task_id, positions in assignments.groupby("task_id").groups.items():  # type: ignore[attr-defined]
        resource_id = assignments.loc[next(iter(positions)), "resource_id"]  # type: ignore[attr-defined]
        assignments.loc[list(positions), "resource_id"] = resource_id  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="task with multiple resources"):
        validator._validate_required_join_cases(no_many_to_many)


def test_guard_rejects_controlled_imperfections_in_base(
    valid_generation: tuple,
) -> None:
    generator, tables = valid_generation
    validator = ProjectManagementGeneratedTablesValidator(generator.generator)

    controlled_null = _copy_tables(tables)
    controlled_null["projects"].loc[0, "end_date"] = ""  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="open_ended_projects NULLs"):
        validator._validate_clean_stage(controlled_null)

    outlier = _copy_tables(tables)
    outlier["tasks"].loc[0, "estimate_hours"] = "500.00"  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="task estimate outliers"):
        validator._validate_clean_stage(outlier)

    duplicate = _copy_tables(tables)
    fields = generator.project_management_config["imperfection_targets"][
        "near_duplicate_time_entries"
    ]["business_key_fields"]
    duplicate["time_entries"].loc[1, fields] = duplicate["time_entries"].loc[  # type: ignore[attr-defined]
        0, fields
    ]
    with pytest.raises(ValueError, match="near-duplicate business keys"):
        validator._validate_clean_stage(duplicate)

    boundary = _copy_tables(tables)
    boundary["tasks"].loc[0, "due_date"] = (  # type: ignore[attr-defined]
        generator.settings.reference_today.isoformat()
    )
    with pytest.raises(ValueError, match="boundary dates in tasks.due_date"):
        validator._validate_clean_stage(boundary)


def test_guard_rejects_non_usd_currency(valid_generation: tuple) -> None:
    generator, tables = valid_generation
    currency = _copy_tables(tables)
    currency["resources"].loc[0, "currency_code"] = "EUR"  # type: ignore[attr-defined]

    with pytest.raises(ValueError, match="resources contains non-USD"):
        ProjectManagementGeneratedTablesValidator(generator.generator).validate(
            currency
        )
