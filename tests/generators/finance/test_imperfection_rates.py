from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import importlib.util
from pathlib import Path

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.core.csv_export import CSVExporter
from generators.finance.distributions import FinanceDistributionApplier
from generators.finance.imperfections import FinanceImperfectionInjector
from generators.finance.validators.imperfection_rates import (
    FinanceImperfectionRateValidator,
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
def distributed_and_imperfect() -> tuple[dict, dict]:
    injector = FinanceImperfectionInjector.for_profile("dev")
    distributed = FinanceDistributionApplier(
        injector.generator
    ).generate_distributed_tables()
    imperfect = injector.apply_to_tables(distributed)
    return distributed, imperfect


def test_expected_dev_imperfection_counts_are_deterministic() -> None:
    validator = FinanceImperfectionRateValidator.for_profile("dev")

    assert validator.expected_null_count() == 10
    assert validator.expected_duplicate_count() == 1
    assert validator.expected_outlier_count() == 5


def test_expected_full_imperfection_counts_are_deterministic() -> None:
    validator = FinanceImperfectionRateValidator.for_profile("full")

    assert validator.expected_null_count() == 92
    assert validator.expected_duplicate_count() == 8
    assert validator.expected_outlier_count() == 500


def test_generated_validation_passes_all_checks() -> None:
    results = FinanceImperfectionRateValidator.for_profile(
        "dev"
    ).generate_and_validate()
    checks = _results_by_name(results)

    assert len(results) == 15
    assert all(result.passed for result in results)
    assert checks["fx_rates.rate.null_rate"].passed
    assert checks["fx_rates.rate.protected_pair"].passed
    assert checks["fx_rates.rate.protected_boundaries"].passed
    assert checks["budgets.near_duplicate_rate"].passed
    assert checks["budgets.near_duplicate_variations"].passed
    assert checks["transactions.total_amount.outlier_rate"].passed
    assert checks["transactions.transaction_date.boundary_values"].passed
    assert checks["finance.imperfection_scope.unapproved_fields"].passed
    assert checks["finance.accounting_fx_invariants"].passed


def test_fx_validation_detects_rate_and_protection_failures(
    distributed_and_imperfect: tuple[dict, dict],
) -> None:
    distributed, imperfect = distributed_and_imperfect
    validator = FinanceImperfectionRateValidator.for_profile("dev")
    malformed = _copy_tables(imperfect)
    existing_missing = malformed["fx_rates"].index[
        malformed["fx_rates"]["rate"].astype(str).eq("")
    ][0]
    identity = malformed["fx_rates"].index[
        malformed["fx_rates"]["from_currency"].eq("USD")
    ][0]
    malformed["fx_rates"].at[existing_missing, "rate"] = distributed["fx_rates"].at[
        existing_missing, "rate"
    ]
    malformed["fx_rates"].at[identity, "rate"] = ""

    checks = _results_by_name(
        validator.validate_tables(malformed, source_tables=distributed)
    )

    assert checks["fx_rates.rate.null_rate"].passed
    assert not checks["fx_rates.rate.protected_pair"].passed
    assert not checks["finance.accounting_fx_invariants"].passed


def test_duplicate_validation_rejects_unapproved_variation(
    distributed_and_imperfect: tuple[dict, dict],
) -> None:
    distributed, imperfect = distributed_and_imperfect
    validator = FinanceImperfectionRateValidator.for_profile("dev")
    malformed = _copy_tables(imperfect)
    duplicate_position = len(distributed["budgets"])
    malformed["budgets"].at[duplicate_position, "currency_code"] = "EUR"

    checks = _results_by_name(
        validator.validate_tables(malformed, source_tables=distributed)
    )

    assert checks["budgets.near_duplicate_rate"].passed
    assert not checks["budgets.near_duplicate_variations"].passed


def test_outlier_validation_detects_count_range_and_scale_failures(
    distributed_and_imperfect: tuple[dict, dict],
) -> None:
    distributed, imperfect = distributed_and_imperfect
    validator = FinanceImperfectionRateValidator.for_profile("dev")
    malformed = _copy_tables(imperfect)
    target = validator.finance_config["imperfection_targets"][
        "transaction_amount_outliers"
    ]
    minimum = Decimal(str(target["minimum_value"]))
    outlier_position = malformed["transactions"].index[
        malformed["transactions"]["total_amount"].map(Decimal).ge(minimum)
    ][0]
    malformed["transactions"].at[outlier_position, "total_amount"] = (
        "100000001.0"
    )

    checks = _results_by_name(
        validator.validate_tables(malformed, source_tables=distributed)
    )

    assert checks["transactions.total_amount.outlier_rate"].passed
    assert not checks["transactions.total_amount.outlier_range"].passed
    assert not checks["transactions.total_amount.outlier_scale"].passed


def test_boundary_validation_detects_missing_value(
    distributed_and_imperfect: tuple[dict, dict],
) -> None:
    distributed, imperfect = distributed_and_imperfect
    validator = FinanceImperfectionRateValidator.for_profile("dev")
    malformed = _copy_tables(imperfect)
    boundary = validator.config["boundary_dates"][0]
    position = malformed["transactions"].index[
        malformed["transactions"]["transaction_date"].eq(boundary)
    ][0]
    malformed["transactions"].at[position, "transaction_date"] = "2025-01-01"

    checks = _results_by_name(
        validator.validate_tables(malformed, source_tables=distributed)
    )

    assert not checks["transactions.transaction_date.boundary_values"].passed
    assert not checks[
        "transactions.transaction_date.boundary_occurrences"
    ].passed


def test_scope_validation_detects_unapproved_field_change(
    distributed_and_imperfect: tuple[dict, dict],
) -> None:
    distributed, imperfect = distributed_and_imperfect
    validator = FinanceImperfectionRateValidator.for_profile("dev")
    malformed = _copy_tables(imperfect)
    malformed["accounts"].at[0, "account_name"] = "Unexpected Replacement"

    checks = _results_by_name(
        validator.validate_tables(malformed, source_tables=distributed)
    )

    assert not checks["finance.imperfection_scope.unapproved_fields"].passed


def test_invariant_validation_detects_ledger_imbalance(
    distributed_and_imperfect: tuple[dict, dict],
) -> None:
    distributed, imperfect = distributed_and_imperfect
    validator = FinanceImperfectionRateValidator.for_profile("dev")
    malformed = _copy_tables(imperfect)
    debit_rows = malformed["ledger_entries"]["debit_amount"].astype(str).ne("")
    position = malformed["ledger_entries"].index[debit_rows][0]
    amount = Decimal(str(malformed["ledger_entries"].at[position, "debit_amount"]))
    malformed["ledger_entries"].at[position, "debit_amount"] = (
        f"{amount + Decimal('0.0001'):.4f}"
    )

    checks = _results_by_name(
        validator.validate_tables(malformed, source_tables=distributed)
    )

    assert not checks["finance.accounting_fx_invariants"].passed


def test_exported_validation_reads_profile_csvs(
    distributed_and_imperfect: tuple[dict, dict],
    tmp_path: Path,
) -> None:
    _, imperfect = distributed_and_imperfect
    configured = FinanceImperfectionRateValidator.for_profile("dev")
    settings = replace(configured.settings, output_path=tmp_path)
    validator = FinanceImperfectionRateValidator(DeterministicGenerator(settings))
    CSVExporter(settings.csv_format).export_tables(
        tables=imperfect,
        table_order=settings.table_order,
        output_dir=tmp_path,
    )

    results = validator.validate_exported_csvs()

    assert all(result.passed for result in results)


def test_exported_validation_reports_missing_csvs(tmp_path: Path) -> None:
    validator = FinanceImperfectionRateValidator.for_profile("full")

    with pytest.raises(FileNotFoundError, match="accounts.csv"):
        validator._csv_paths(tmp_path)


def test_validator_rejects_non_finance_settings() -> None:
    settings = GenerationSettings.from_config_files("sales", "dev")

    with pytest.raises(ValueError, match="only supports finance"):
        FinanceImperfectionRateValidator(DeterministicGenerator(settings))


def _copy_tables(tables: dict) -> dict:
    return {name: table.copy(deep=True) for name, table in tables.items()}


def _results_by_name(results: list) -> dict:
    return {result.check_name: result for result in results}
