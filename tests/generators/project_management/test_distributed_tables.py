from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import timedelta
import importlib.util

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.project_management.distributions import (
    ProjectManagementDistributionApplier,
)
from generators.project_management.generator import (
    ProjectManagementBaseEntityGenerator,
)
from generators.project_management.validators.distributed_tables import (
    ProjectManagementDistributedTablesValidator,
)
from generators.project_management.validators.distributed_tables import (
    validate_project_management_distributed_tables,
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
        ProjectManagementDistributionApplier,
        dict,
        dict,
    ]
):
    applier = ProjectManagementDistributionApplier.for_profile("dev")
    base = ProjectManagementBaseEntityGenerator(applier.generator).generate_tables()
    distributed = applier.apply_to_tables(base)
    return applier, base, distributed


def test_valid_distributed_tables_pass_with_and_without_source(
    validated_tables: tuple[ProjectManagementDistributionApplier, dict, dict],
) -> None:
    applier, base, distributed = validated_tables

    validate_project_management_distributed_tables(applier.generator, distributed)
    validate_project_management_distributed_tables(
        applier.generator,
        distributed,
        source_tables=base,
    )


def test_rejects_budget_outside_effective_pareto_bounds(
    validated_tables: tuple[ProjectManagementDistributionApplier, dict, dict],
) -> None:
    applier, _, distributed = validated_tables
    malformed = _copy_tables(distributed)
    position = malformed["projects"].index[
        malformed["projects"]["budget_amount"] != ""
    ][0]
    malformed["projects"].at[position, "budget_amount"] = "49999.00"

    with pytest.raises(ValueError, match="outside configured distribution bounds"):
        validate_project_management_distributed_tables(applier.generator, malformed)


def test_rejects_budget_with_wrong_decimal_scale(
    validated_tables: tuple[ProjectManagementDistributionApplier, dict, dict],
) -> None:
    applier, _, distributed = validated_tables
    malformed = _copy_tables(distributed)
    position = malformed["projects"].index[
        malformed["projects"]["budget_amount"] != ""
    ][0]
    malformed["projects"].at[position, "budget_amount"] = "50000.0"

    with pytest.raises(ValueError, match="scale 2"):
        validate_project_management_distributed_tables(applier.generator, malformed)


def test_rejects_amounts_without_observable_pareto_tail(
    validated_tables: tuple[ProjectManagementDistributionApplier, dict, dict],
) -> None:
    applier, _, distributed = validated_tables
    malformed = _copy_tables(distributed)
    populated = malformed["projects"]["budget_amount"] != ""
    malformed["projects"].loc[populated, "budget_amount"] = "50000.00"

    with pytest.raises(ValueError, match="does not show an observable Pareto tail"):
        validate_project_management_distributed_tables(applier.generator, malformed)


def test_rejects_uniform_time_entry_frequency(
    validated_tables: tuple[ProjectManagementDistributionApplier, dict, dict],
) -> None:
    applier, _, distributed = validated_tables
    malformed = _copy_tables(distributed)
    assignments = list(malformed["task_resources"].itertuples(index=False))
    entries = malformed["time_entries"]
    work_types = applier.config["domain_values"]["work_types"]
    rows_per_assignment = len(entries) // len(assignments)
    assert rows_per_assignment == len(work_types)

    position = 0
    for assignment in assignments:
        entry_date = date.fromisoformat(assignment.assigned_at)
        for offset in range(rows_per_assignment):
            entries.at[position, "task_id"] = int(assignment.task_id)
            entries.at[position, "resource_id"] = int(assignment.resource_id)
            entries.at[position, "work_type"] = work_types[offset]
            entries.at[position, "entry_date"] = entry_date.isoformat()
            original = datetime.fromisoformat(entries.at[position, "created_at"])
            entries.at[position, "created_at"] = datetime.combine(
                entry_date,
                original.time(),
            ).strftime("%Y-%m-%dT%H:%M:%S")
            position += 1

    with pytest.raises(ValueError, match="do not vary between assignments"):
        validate_project_management_distributed_tables(applier.generator, malformed)


def test_rejects_dates_without_configured_gaussian_clustering(
    validated_tables: tuple[ProjectManagementDistributionApplier, dict, dict],
) -> None:
    applier, _, distributed = validated_tables
    malformed = _copy_tables(distributed)
    start = date.fromisoformat(applier.rules["date_windows"]["project_activity_start"])
    horizon = date.fromisoformat(
        applier.rules["date_windows"]["project_planning_horizon_end"]
    )
    latest = horizon - timedelta(
        days=int(applier.rules["projects"]["duration_days"]["minimum"])
    )
    off_center = start + timedelta(days=round((latest - start).days * 0.4))
    malformed["projects"]["start_date"] = off_center.isoformat()

    validator = ProjectManagementDistributedTablesValidator(applier.generator)
    with pytest.raises(ValueError, match="does not show configured Gaussian"):
        validator._validate_date_clustering(malformed)


def test_rejects_primary_key_drift_from_source(
    validated_tables: tuple[ProjectManagementDistributionApplier, dict, dict],
) -> None:
    applier, base, distributed = validated_tables
    malformed = _copy_tables(distributed)
    replacement = 999999
    malformed["resources"].at[0, "resource_id"] = replacement
    malformed["task_resources"].loc[
        malformed["task_resources"]["resource_id"] == 1,
        "resource_id",
    ] = replacement
    malformed["time_entries"].loc[
        malformed["time_entries"]["resource_id"] == 1,
        "resource_id",
    ] = replacement

    with pytest.raises(ValueError, match="resources.resource_id changed"):
        validate_project_management_distributed_tables(
            applier.generator,
            malformed,
            source_tables=base,
        )


def test_rejects_unapproved_field_drift_from_source(
    validated_tables: tuple[ProjectManagementDistributionApplier, dict, dict],
) -> None:
    applier, base, distributed = validated_tables
    malformed = _copy_tables(distributed)
    malformed["projects"].at[0, "project_name"] = "Unexpected Replacement"

    with pytest.raises(ValueError, match="projects.project_name changed unexpectedly"):
        validate_project_management_distributed_tables(
            applier.generator,
            malformed,
            source_tables=base,
        )


def test_rejects_business_null_position_drift_from_source(
    validated_tables: tuple[ProjectManagementDistributionApplier, dict, dict],
) -> None:
    applier, base, distributed = validated_tables
    malformed = _copy_tables(distributed)
    position = malformed["projects"].index[
        malformed["projects"]["budget_amount"] == ""
    ][0]
    malformed["projects"].at[position, "budget_amount"] = "50000.00"

    with pytest.raises(ValueError, match="business NULL positions changed"):
        validate_project_management_distributed_tables(
            applier.generator,
            malformed,
            source_tables=base,
        )


def test_validator_rejects_non_project_management_settings() -> None:
    settings = GenerationSettings.from_config_files("sales", "dev")
    with pytest.raises(ValueError, match="only supports"):
        ProjectManagementDistributedTablesValidator(DeterministicGenerator(settings))


def _copy_tables(tables: dict) -> dict:
    return {name: table.copy(deep=True) for name, table in tables.items()}
