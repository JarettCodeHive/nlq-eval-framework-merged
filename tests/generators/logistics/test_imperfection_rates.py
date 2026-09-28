from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import importlib.util
from pathlib import Path

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.csv_export import CSVExporter
from generators.logistics.distributions import LogisticsDistributionApplier
from generators.logistics.imperfections import LogisticsImperfectionInjector
from generators.logistics.validators.imperfection_rates import (
    LogisticsImperfectionRateValidator,
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
    injector = LogisticsImperfectionInjector.for_profile("dev")
    distributed = LogisticsDistributionApplier(
        injector.generator
    ).generate_distributed_tables()
    return distributed, injector.apply_to_tables(distributed)


def test_expected_counts_are_deterministic() -> None:
    dev = LogisticsImperfectionRateValidator.for_profile("dev")
    full = LogisticsImperfectionRateValidator.for_profile("full")

    assert (
        dev.expected_duplicate_count(),
        dev.expected_null_count(),
        dev.expected_orphan_count(),
        dev.expected_outlier_count(),
    ) == (15, 25, 10, 5)
    assert (
        full.expected_duplicate_count(),
        full.expected_null_count(),
        full.expected_orphan_count(),
        full.expected_outlier_count(),
    ) == (1500, 2500, 1000, 500)


def test_generated_validation_passes_all_checks() -> None:
    results = LogisticsImperfectionRateValidator.for_profile(
        "dev"
    ).generate_and_validate()
    checks = _results_by_name(results)

    assert len(results) == 20
    assert all(result.passed for result in results)
    assert checks["shipments.near_duplicate_variations"].passed
    assert checks["orders.warehouse_id.orphan_namespace"].passed
    assert checks["orders.total_amount.outlier_scale"].passed
    assert checks["logistics.coordinated_boundary_paths"].passed
    assert checks["logistics.imperfection_scope.unapproved_fields"].passed
    assert checks["logistics.relational_invariants"].passed


def test_duplicate_variation_failure_is_reported(
    distributed_and_imperfect: tuple[dict, dict],
) -> None:
    distributed, imperfect = distributed_and_imperfect
    malformed = _copy_tables(imperfect)
    duplicate_position = len(distributed["shipments"])
    tracking_number = malformed["shipments"].at[
        duplicate_position, "tracking_number"
    ]
    base_shipments = malformed["shipments"].loc[: duplicate_position - 1]
    original = base_shipments[
        base_shipments["tracking_number"] == tracking_number
    ].iloc[0]
    for field in ("ship_date", "status", "shipping_cost"):
        malformed["shipments"].at[duplicate_position, field] = original[field]
    checks = _results_by_name(
        LogisticsImperfectionRateValidator.for_profile("dev").validate_tables(
            malformed, distributed
        )
    )

    assert checks["shipments.near_duplicate_rate"].passed
    assert not checks["shipments.near_duplicate_variations"].passed


def test_orphan_namespace_and_outlier_scale_failures_are_reported(
    distributed_and_imperfect: tuple[dict, dict],
) -> None:
    distributed, imperfect = distributed_and_imperfect
    malformed = _copy_tables(imperfect)
    warehouse_ids = set(malformed["warehouses"]["warehouse_id"])
    orphan_position = malformed["orders"].index[
        malformed["orders"]["warehouse_id"].map(
            lambda value: value != "" and value not in warehouse_ids
        )
    ][0]
    malformed["orders"].at[orphan_position, "warehouse_id"] = 800_000_000

    target = LogisticsImperfectionRateValidator.for_profile("dev").targets[
        "order_total_outliers"
    ]
    minimum = Decimal(str(target["minimum_value"]))
    outlier_position = malformed["orders"].index[
        malformed["orders"]["total_amount"].map(Decimal).ge(minimum)
    ][0]
    malformed["orders"].at[outlier_position, "total_amount"] = f"{minimum:.1f}"
    checks = _results_by_name(
        LogisticsImperfectionRateValidator.for_profile("dev").validate_tables(
            malformed, distributed
        )
    )

    assert checks["orders.warehouse_id.orphan_rate"].passed
    assert not checks["orders.warehouse_id.orphan_namespace"].passed
    assert not checks["orders.total_amount.outlier_scale"].passed


def test_boundary_and_scope_failures_are_reported(
    distributed_and_imperfect: tuple[dict, dict],
) -> None:
    distributed, imperfect = distributed_and_imperfect
    malformed = _copy_tables(imperfect)
    boundary = LogisticsImperfectionRateValidator.for_profile("dev").config[
        "boundary_dates"
    ][0]
    malformed["orders"].loc[
        malformed["orders"]["order_date"].eq(boundary), "order_date"
    ] = "2025-01-01"
    malformed["carriers"].at[0, "carrier_name"] = "Unexpected Replacement"
    checks = _results_by_name(
        LogisticsImperfectionRateValidator.for_profile("dev").validate_tables(
            malformed, distributed
        )
    )

    assert not checks["orders.order_date.boundary_values"].passed
    assert not checks["logistics.coordinated_boundary_paths"].passed
    assert not checks["logistics.imperfection_scope.unapproved_fields"].passed


def test_exported_validation_reads_profile_csvs(
    distributed_and_imperfect: tuple[dict, dict],
    tmp_path: Path,
) -> None:
    _, imperfect = distributed_and_imperfect
    configured = LogisticsImperfectionRateValidator.for_profile("dev")
    settings = replace(configured.settings, output_path=tmp_path)
    validator = LogisticsImperfectionRateValidator(
        DeterministicGenerator(settings)
    )
    CSVExporter(settings.csv_format).export_tables(
        tables=imperfect,
        table_order=settings.table_order,
        output_dir=tmp_path,
    )

    assert all(result.passed for result in validator.validate_exported_csvs())


def test_missing_csvs_are_reported(tmp_path: Path) -> None:
    validator = LogisticsImperfectionRateValidator.for_profile("dev")

    with pytest.raises(FileNotFoundError, match="carriers.csv"):
        validator.validate_csv_directory(tmp_path)


def _copy_tables(tables: dict) -> dict:
    return {name: table.copy(deep=True) for name, table in tables.items()}


def _results_by_name(results: list) -> dict:
    return {result.check_name: result for result in results}
