from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import importlib.util
from pathlib import Path

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.core.csv_export import CSVExporter
from generators.project_management.distributions import (
    ProjectManagementDistributionApplier,
)
from generators.project_management.imperfections import (
    ProjectManagementImperfectionInjector,
)
from generators.project_management.validators.imperfection_rates import (
    ProjectManagementImperfectionRateValidator,
)


pytestmark = pytest.mark.skipif(
    not all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    ),
    reason="generation dependencies are not installed",
)


@pytest.fixture(scope="module")
def distributed_and_imperfect() -> tuple[dict, dict]:
    injector = ProjectManagementImperfectionInjector.for_profile("dev")
    distributed = ProjectManagementDistributionApplier(
        injector.generator
    ).generate_distributed_tables()
    return distributed, injector.apply_to_tables(distributed)


def test_expected_counts_are_deterministic() -> None:
    dev = ProjectManagementImperfectionRateValidator.for_profile("dev")
    full = ProjectManagementImperfectionRateValidator.for_profile("full")

    assert (dev.expected_duplicate_count(), dev.expected_outlier_count()) == (15, 1)
    assert (dev.expected_null_count("projects"), dev.expected_null_count("tasks")) == (
        1,
        3,
    )
    assert (full.expected_duplicate_count(), full.expected_outlier_count()) == (
        1500,
        50,
    )


def test_generated_validation_passes_all_checks() -> None:
    results = ProjectManagementImperfectionRateValidator.for_profile(
        "dev"
    ).generate_and_validate()
    checks = _results_by_name(results)

    assert len(results) == 18
    assert all(result.passed for result in results)
    assert checks["time_entries.near_duplicate_variations"].passed
    assert checks["projects.end_date.null_rate"].passed
    assert checks["tasks.estimate_hours.outlier_rate"].passed
    assert checks["projects.start_date.boundary_values"].passed
    assert checks["project_management.imperfection_scope.unapproved_fields"].passed
    assert checks["project_management.relational_date_invariants"].passed


def test_duplicate_variation_failure_is_reported(
    distributed_and_imperfect: tuple[dict, dict],
) -> None:
    distributed, imperfect = distributed_and_imperfect
    malformed = _copy_tables(imperfect)
    duplicate_position = len(distributed["time_entries"])
    malformed["time_entries"].at[duplicate_position, "billable"] = not bool(
        malformed["time_entries"].at[duplicate_position, "billable"]
    )
    checks = _results_by_name(
        ProjectManagementImperfectionRateValidator.for_profile(
            "dev"
        ).validate_tables(malformed, distributed)
    )

    assert checks["time_entries.near_duplicate_rate"].passed
    assert not checks["time_entries.near_duplicate_variations"].passed


def test_null_eligibility_and_outlier_scale_failures_are_reported(
    distributed_and_imperfect: tuple[dict, dict],
) -> None:
    distributed, imperfect = distributed_and_imperfect
    malformed = _copy_tables(imperfect)
    null_project = malformed["projects"].index[
        malformed["projects"]["end_date"].eq("")
    ][0]
    malformed["projects"].at[null_project, "status"] = "Completed"
    target = ProjectManagementImperfectionRateValidator.for_profile(
        "dev"
    ).targets["task_estimate_outliers"]
    minimum = Decimal(str(target["minimum_value"]))
    outlier = malformed["tasks"].index[
        malformed["tasks"]["estimate_hours"].map(Decimal).ge(minimum)
    ][0]
    malformed["tasks"].at[outlier, "estimate_hours"] = "500.0"
    checks = _results_by_name(
        ProjectManagementImperfectionRateValidator.for_profile(
            "dev"
        ).validate_tables(malformed, distributed)
    )

    assert not checks["projects.end_date.eligible_status"].passed
    assert not checks["tasks.estimate_hours.outlier_scale"].passed


def test_boundary_and_scope_failures_are_reported(
    distributed_and_imperfect: tuple[dict, dict],
) -> None:
    distributed, imperfect = distributed_and_imperfect
    malformed = _copy_tables(imperfect)
    boundary = ProjectManagementImperfectionRateValidator.for_profile(
        "dev"
    ).config["boundary_dates"][0]
    malformed["projects"].loc[
        malformed["projects"]["start_date"].eq(boundary), "start_date"
    ] = "2025-01-01"
    malformed["resources"].at[0, "resource_name"] = "Unexpected Replacement"
    checks = _results_by_name(
        ProjectManagementImperfectionRateValidator.for_profile(
            "dev"
        ).validate_tables(malformed, distributed)
    )

    assert not checks["projects.start_date.boundary_values"].passed
    assert not checks[
        "project_management.imperfection_scope.unapproved_fields"
    ].passed


def test_relational_invariant_failure_is_reported(
    distributed_and_imperfect: tuple[dict, dict],
) -> None:
    distributed, imperfect = distributed_and_imperfect
    malformed = _copy_tables(imperfect)
    malformed["task_resources"].at[0, "allocation_pct"] = "99.99"
    checks = _results_by_name(
        ProjectManagementImperfectionRateValidator.for_profile(
            "dev"
        ).validate_tables(malformed, distributed)
    )

    assert not checks["project_management.relational_date_invariants"].passed


def test_exported_validation_reads_profile_csvs(
    distributed_and_imperfect: tuple[dict, dict],
    tmp_path: Path,
) -> None:
    _, imperfect = distributed_and_imperfect
    configured = ProjectManagementImperfectionRateValidator.for_profile("dev")
    settings = replace(configured.settings, output_path=tmp_path)
    validator = ProjectManagementImperfectionRateValidator(
        DeterministicGenerator(settings)
    )
    CSVExporter(settings.csv_format).export_tables(
        tables=imperfect,
        table_order=settings.table_order,
        output_dir=tmp_path,
    )

    assert all(result.passed for result in validator.validate_exported_csvs())


def test_missing_csvs_are_reported(tmp_path: Path) -> None:
    validator = ProjectManagementImperfectionRateValidator.for_profile("full")

    with pytest.raises(FileNotFoundError, match="projects.csv"):
        validator.validate_csv_directory(tmp_path)


def test_validator_rejects_non_pm_settings() -> None:
    settings = GenerationSettings.from_config_files("sales", "dev")
    with pytest.raises(ValueError, match="only supports project_management"):
        ProjectManagementImperfectionRateValidator(DeterministicGenerator(settings))


def _copy_tables(tables: dict) -> dict:
    return {name: table.copy(deep=True) for name, table in tables.items()}


def _results_by_name(results: list) -> dict:
    return {result.check_name: result for result in results}
