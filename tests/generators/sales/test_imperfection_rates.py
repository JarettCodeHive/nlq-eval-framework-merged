from __future__ import annotations

from dataclasses import replace
import importlib.util
from pathlib import Path

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.core.csv_export import CSVExporter
from generators.sales.imperfections import SalesImperfectionInjector
from generators.sales.validators.imperfection_rates import (
    SalesImperfectionRateValidator,
)


def _dependencies_available() -> bool:
    return all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    )


pytestmark = pytest.mark.skipif(
    not _dependencies_available(),
    reason="numpy, pandas, and Faker are not installed",
)


@pytest.fixture(scope="module")
def imperfect_tables() -> dict:
    return SalesImperfectionInjector.for_profile("dev").generate_imperfect_tables()


def test_expected_dev_imperfection_counts_are_deterministic() -> None:
    validator = SalesImperfectionRateValidator.for_profile("dev")

    assert validator.expected_duplicate_count() == 3
    assert validator.expected_null_count() == 2
    assert validator.expected_outlier_count() == 1


def test_expected_full_imperfection_counts_are_deterministic() -> None:
    validator = SalesImperfectionRateValidator.for_profile("full")

    assert validator.expected_duplicate_count() == 300
    assert validator.expected_null_count() == 13
    assert validator.expected_outlier_count() == 25


def test_generated_validation_passes_all_checks() -> None:
    results = SalesImperfectionRateValidator.for_profile("dev").generate_and_validate()
    checks = _results_by_name(results)

    assert len(results) == 8
    assert all(result.passed for result in results)
    assert checks["quotations.near_duplicate_rate"].passed
    assert checks["quotations.near_duplicate_variations"].passed
    assert checks["products.list_price.null_rate"].passed
    assert checks["deals.deal_amount.outlier_rate"].passed
    assert checks["deals.deal_amount.outlier_range"].passed
    assert checks["deals.deal_amount.outlier_scale"].passed
    assert checks["products.created_at.boundary_values"].passed
    assert checks["products.created_at.boundary_occurrences"].passed


def test_duplicate_validation_uses_business_keys_and_permitted_variations(
    imperfect_tables: dict,
) -> None:
    validator = SalesImperfectionRateValidator.for_profile("dev")
    malformed = _copy_tables(imperfect_tables)
    business_keys = ["deal_id", "product_id", "quote_number"]
    duplicate_rows = malformed["quotations"].duplicated(business_keys, keep=False)
    duplicate_position = int(malformed["quotations"].index[duplicate_rows][-1])
    malformed["quotations"].at[duplicate_position, "unit_price"] = "999.99"

    checks = _results_by_name(validator.validate_tables(malformed))

    assert checks["quotations.near_duplicate_rate"].passed
    assert not checks["quotations.near_duplicate_variations"].passed


def test_rate_validation_detects_incorrect_null_outlier_and_boundary_counts(
    imperfect_tables: dict,
) -> None:
    validator = SalesImperfectionRateValidator.for_profile("dev")
    malformed = _copy_tables(imperfect_tables)
    populated = malformed["products"]["list_price"].astype(str).ne("")
    malformed["products"].loc[populated.idxmax(), "list_price"] = ""
    malformed["deals"].loc[0, "deal_amount"] = "3000000.00"
    malformed["products"].loc[0, "created_at"] = "2024-01-01T00:00:00"

    checks = _results_by_name(validator.validate_tables(malformed))

    assert not checks["products.list_price.null_rate"].passed
    assert not checks["deals.deal_amount.outlier_rate"].passed
    assert not checks["products.created_at.boundary_values"].passed
    assert not checks["products.created_at.boundary_occurrences"].passed


def test_outlier_validation_detects_invalid_range_and_scale(
    imperfect_tables: dict,
) -> None:
    validator = SalesImperfectionRateValidator.for_profile("dev")
    malformed = _copy_tables(imperfect_tables)
    outlier_position = int(
        malformed["deals"].index[
            malformed["deals"]["deal_amount"].astype(float).ge(2_500_000)
        ][0]
    )
    malformed["deals"].loc[outlier_position, "deal_amount"] = "10000001.0"

    checks = _results_by_name(validator.validate_tables(malformed))

    assert checks["deals.deal_amount.outlier_rate"].passed
    assert not checks["deals.deal_amount.outlier_range"].passed
    assert not checks["deals.deal_amount.outlier_scale"].passed


def test_exported_validation_reads_profile_csvs(
    imperfect_tables: dict,
    tmp_path: Path,
) -> None:
    configured = SalesImperfectionRateValidator.for_profile("dev")
    settings = replace(configured.settings, output_path=tmp_path)
    validator = SalesImperfectionRateValidator(DeterministicGenerator(settings))
    CSVExporter(settings.csv_format).export_tables(
        tables=imperfect_tables,
        table_order=settings.table_order,
        output_dir=tmp_path,
    )

    results = validator.validate_exported_csvs()

    assert all(result.passed for result in results)


def test_exported_validation_reports_missing_csvs(tmp_path: Path) -> None:
    validator = SalesImperfectionRateValidator.for_profile("full")

    with pytest.raises(FileNotFoundError, match="leads.csv"):
        validator._csv_paths(tmp_path)


def test_validator_rejects_non_sales_settings() -> None:
    settings = GenerationSettings.from_config_files("crm", "dev")

    with pytest.raises(ValueError, match="only supports sales"):
        SalesImperfectionRateValidator(DeterministicGenerator(settings))


def _copy_tables(tables: dict) -> dict:
    return {name: table.copy(deep=True) for name, table in tables.items()}


def _results_by_name(results: list) -> dict:
    return {result.check_name: result for result in results}
