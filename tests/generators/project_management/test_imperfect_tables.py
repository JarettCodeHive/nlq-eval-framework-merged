from __future__ import annotations

import importlib.util

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.project_management.distributions import (
    ProjectManagementDistributionApplier,
)
from generators.project_management.imperfections import (
    ProjectManagementImperfectionInjector,
)
from generators.project_management.validators.imperfect_tables import (
    ProjectManagementImperfectTablesValidator,
)
from generators.project_management.validators.imperfect_tables import (
    _imperfection_mutable_fields,
)
from generators.project_management.validators.imperfect_tables import (
    validate_project_management_imperfect_tables,
)


pytestmark = pytest.mark.skipif(
    not all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    ),
    reason="numpy, pandas, and Faker are required",
)


@pytest.fixture(scope="module")
def validated_tables() -> (
    tuple[
        ProjectManagementImperfectionInjector,
        dict,
        dict,
    ]
):
    distributed = ProjectManagementDistributionApplier.for_profile(
        "dev"
    ).generate_distributed_tables()
    injector = ProjectManagementImperfectionInjector.for_profile("dev")
    imperfect = injector.apply_to_tables(distributed)
    return injector, distributed, imperfect


def test_valid_imperfect_tables_pass_with_and_without_source(
    validated_tables: tuple[ProjectManagementImperfectionInjector, dict, dict],
) -> None:
    injector, distributed, imperfect = validated_tables

    validate_project_management_imperfect_tables(injector.generator, imperfect)
    validate_project_management_imperfect_tables(
        injector.generator,
        imperfect,
        source_tables=distributed,
    )


def test_rejects_duplicate_time_entry_row_count_drift(
    validated_tables: tuple[ProjectManagementImperfectionInjector, dict, dict],
) -> None:
    injector, _, imperfect = validated_tables
    malformed = _copy_tables(imperfect)
    malformed["time_entries"] = malformed["time_entries"].iloc[:-1].copy()

    with pytest.raises(ValueError, match="imperfect row count differs"):
        validate_project_management_imperfect_tables(injector.generator, malformed)


def test_rejects_near_duplicate_non_variation_change(
    validated_tables: tuple[ProjectManagementImperfectionInjector, dict, dict],
) -> None:
    injector, _, imperfect = validated_tables
    malformed = _copy_tables(imperfect)
    position = injector.generator.row_count("time_entries")
    malformed["time_entries"].at[position, "billable"] = not bool(
        malformed["time_entries"].at[position, "billable"]
    )

    with pytest.raises(ValueError, match="changed a non-variation field"):
        validate_project_management_imperfect_tables(injector.generator, malformed)


@pytest.mark.parametrize(
    ("table_name", "field_name"),
    [("projects", "end_date"), ("tasks", "due_date")],
)
def test_rejects_open_ended_null_count_drift(
    validated_tables: tuple[ProjectManagementImperfectionInjector, dict, dict],
    table_name: str,
    field_name: str,
) -> None:
    injector, distributed, imperfect = validated_tables
    malformed = _copy_tables(imperfect)
    position = int(
        malformed[table_name].index[malformed[table_name][field_name] == ""][0]
    )
    malformed[table_name].at[position, field_name] = distributed[table_name].at[
        position,
        field_name,
    ]

    with pytest.raises(ValueError, match="NULL count differs"):
        validate_project_management_imperfect_tables(injector.generator, malformed)


def test_rejects_open_ended_ineligible_status(
    validated_tables: tuple[ProjectManagementImperfectionInjector, dict, dict],
) -> None:
    injector, _, imperfect = validated_tables
    malformed = _copy_tables(imperfect)
    position = int(
        malformed["projects"].index[malformed["projects"]["end_date"] == ""][0]
    )
    malformed["projects"].at[position, "status"] = "Completed"

    with pytest.raises(ValueError, match="contains an ineligible status"):
        validate_project_management_imperfect_tables(injector.generator, malformed)


def test_rejects_task_estimate_outlier_outside_range(
    validated_tables: tuple[ProjectManagementImperfectionInjector, dict, dict],
) -> None:
    injector, _, imperfect = validated_tables
    malformed = _copy_tables(imperfect)
    position = int(
        malformed["tasks"].index[
            malformed["tasks"]["estimate_hours"].astype(float) >= 500
        ][0]
    )
    malformed["tasks"].at[position, "estimate_hours"] = "2000.01"

    with pytest.raises(ValueError, match="exceeds its maximum"):
        validate_project_management_imperfect_tables(injector.generator, malformed)


def test_rejects_missing_configured_boundary_value(
    validated_tables: tuple[ProjectManagementImperfectionInjector, dict, dict],
) -> None:
    injector, _, imperfect = validated_tables
    malformed = _copy_tables(imperfect)
    boundary = injector.config["boundary_dates"][0]
    malformed["projects"].loc[
        malformed["projects"]["start_date"] == boundary,
        "start_date",
    ] = "1901-01-01"

    with pytest.raises(ValueError, match="is missing configured boundary dates"):
        validate_project_management_imperfect_tables(injector.generator, malformed)


def test_rejects_unapproved_source_field_drift(
    validated_tables: tuple[ProjectManagementImperfectionInjector, dict, dict],
) -> None:
    injector, distributed, imperfect = validated_tables
    malformed = _copy_tables(imperfect)
    malformed["resources"].at[0, "resource_name"] = "Unexpected Replacement"

    with pytest.raises(
        ValueError, match="resources.resource_name changed unexpectedly"
    ):
        validate_project_management_imperfect_tables(
            injector.generator,
            malformed,
            source_tables=distributed,
        )


def test_rejects_duplicate_primary_key(
    validated_tables: tuple[ProjectManagementImperfectionInjector, dict, dict],
) -> None:
    injector, _, imperfect = validated_tables
    malformed = _copy_tables(imperfect)
    position = injector.generator.row_count("time_entries")
    malformed["time_entries"].at[position, "entry_id"] = 1

    with pytest.raises(ValueError, match="contains duplicate primary keys"):
        validate_project_management_imperfect_tables(injector.generator, malformed)


def test_mutable_fields_are_derived_from_complete_config_contract(
    validated_tables: tuple[ProjectManagementImperfectionInjector, dict, dict],
) -> None:
    injector, _, _ = validated_tables
    mutable = _imperfection_mutable_fields(injector.project_management_config)

    assert mutable["projects"] == {"start_date", "end_date", "created_at"}
    assert mutable["tasks"] == {
        "start_date",
        "due_date",
        "completed_date",
        "estimate_hours",
        "created_at",
    }
    assert mutable["task_resources"] == {
        "assigned_at",
        "released_at",
        "created_at",
    }
    assert mutable["milestones"] == {
        "planned_date",
        "actual_date",
        "created_at",
    }
    assert mutable["time_entries"] == {"entry_date", "created_at"}
    assert mutable["resources"] == set()


def test_validator_rejects_non_project_management_settings() -> None:
    settings = GenerationSettings.from_config_files("crm", "dev")
    with pytest.raises(ValueError, match="only supports"):
        ProjectManagementImperfectTablesValidator(DeterministicGenerator(settings))


def _copy_tables(tables: dict) -> dict:
    return {name: table.copy(deep=True) for name, table in tables.items()}
