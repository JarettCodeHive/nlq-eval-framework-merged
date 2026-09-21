from __future__ import annotations

from dataclasses import replace
import importlib.util
from pathlib import Path

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.core.csv_export import CSVExporter
from generators.finance.imperfections import FinanceImperfectionInjector
from generators.finance.validators.fk_integrity import ACCOUNTING_CHECKS
from generators.finance.validators.fk_integrity import FinanceDuckDBFKValidator
from generators.finance.validators.fk_integrity import MANUAL_FK_CHECKS
from generators.finance.validators.fk_integrity import UNIQUE_KEY_CHECKS


EXPECTED_TABLE_ORDER = (
    "accounts",
    "transactions",
    "ledger_entries",
    "budgets",
    "fx_rates",
)


def _dependencies_available() -> bool:
    return all(
        importlib.util.find_spec(package) is not None
        for package in ("duckdb", "numpy", "pandas", "faker")
    )


pytestmark = pytest.mark.skipif(
    not _dependencies_available(),
    reason="duckdb, numpy, pandas, and Faker are not installed",
)


@pytest.fixture(scope="module")
def imperfect_tables() -> dict:
    return FinanceImperfectionInjector.for_profile(
        "dev"
    ).generate_imperfect_tables()


def test_finance_duckdb_validator_uses_canonical_schema() -> None:
    validator = FinanceDuckDBFKValidator.for_profile("full")

    assert validator.settings.table_order == EXPECTED_TABLE_ORDER
    assert validator.settings.output_path.name == "dataset-v1.0.0"
    assert validator.settings.schema_source.name == "finance_ddl.sql"


def test_manual_checks_cover_only_physical_finance_foreign_keys() -> None:
    assert set(MANUAL_FK_CHECKS) == {
        "accounts.parent_account_id.fk",
        "ledger_entries.transaction_id.fk",
        "ledger_entries.account_id.fk",
        "budgets.account_id.fk",
    }
    sql = " ".join(MANUAL_FK_CHECKS.values()).lower()
    assert "pragma foreign_key_check" not in sql
    assert "fx_rates" not in sql


def test_post_load_checks_cover_finance_unique_keys_and_accounting() -> None:
    assert set(UNIQUE_KEY_CHECKS) == {
        "accounts.account_id.unique",
        "accounts.account_number.unique",
        "transactions.transaction_id.unique",
        "ledger_entries.entry_id.unique",
        "ledger_entries.transaction_line.unique",
        "budgets.budget_id.unique",
        "fx_rates.rate_id.unique",
        "fx_rates.currency_date.unique",
    }
    assert set(ACCOUNTING_CHECKS) == {
        "ledger_entries.debit_credit_exclusivity",
        "ledger_entries.transaction_balance",
        "ledger_entries.transaction_amount_alignment",
        "ledger_entries.transaction_currency",
    }


def test_generated_fk_validation_passes_all_checks() -> None:
    results = FinanceDuckDBFKValidator.for_profile("dev").generate_and_validate()
    checks = _results_by_name(results)

    assert len(results) == 22
    assert all(result.passed for result in results)
    assert checks["schema.duckdb_ddl"].passed
    for table_name in EXPECTED_TABLE_ORDER:
        assert checks[f"{table_name}.duckdb_load"].passed


@pytest.mark.parametrize(
    ("table_name", "column_name", "invalid_value", "failed_load"),
    [
        ("accounts", "parent_account_id", 999999, "accounts.duckdb_load"),
        (
            "ledger_entries",
            "transaction_id",
            999999,
            "ledger_entries.duckdb_load",
        ),
        ("ledger_entries", "account_id", 999999, "ledger_entries.duckdb_load"),
        ("budgets", "account_id", 999999, "budgets.duckdb_load"),
    ],
)
def test_constrained_load_rejects_orphan_foreign_keys(
    imperfect_tables: dict,
    tmp_path: Path,
    table_name: str,
    column_name: str,
    invalid_value: int,
    failed_load: str,
) -> None:
    validator = FinanceDuckDBFKValidator.for_profile("dev")
    malformed = _copy_tables(imperfect_tables)
    row_index = 1 if table_name == "accounts" else 0
    malformed[table_name].loc[row_index, column_name] = invalid_value

    checks = _results_by_name(
        validator._validate_csv_paths(_export_tables(validator, malformed, tmp_path))
    )

    assert checks["schema.duckdb_ddl"].passed
    assert not checks[failed_load].passed


def test_fx_lookup_is_not_treated_as_a_physical_foreign_key(
    imperfect_tables: dict,
    tmp_path: Path,
) -> None:
    validator = FinanceDuckDBFKValidator.for_profile("dev")
    modified = _copy_tables(imperfect_tables)
    modified["transactions"].loc[0, "transaction_date"] = "2098-01-01"

    results = validator._validate_csv_paths(
        _export_tables(validator, modified, tmp_path)
    )

    assert all(result.passed for result in results)


def test_post_load_accounting_check_reports_unbalanced_transaction(
    imperfect_tables: dict,
    tmp_path: Path,
) -> None:
    validator = FinanceDuckDBFKValidator.for_profile("dev")
    malformed = _copy_tables(imperfect_tables)
    debit_rows = malformed["ledger_entries"]["debit_amount"].astype(str).ne("")
    row_index = malformed["ledger_entries"].index[debit_rows][0]
    current = float(malformed["ledger_entries"].loc[row_index, "debit_amount"])
    malformed["ledger_entries"].loc[row_index, "debit_amount"] = f"{current + 1:.4f}"

    checks = _results_by_name(
        validator._validate_csv_paths(_export_tables(validator, malformed, tmp_path))
    )

    assert checks["ledger_entries.duckdb_load"].passed
    assert not checks["ledger_entries.transaction_balance"].passed
    assert not checks["ledger_entries.transaction_amount_alignment"].passed


def test_ddl_unique_constraint_rejects_duplicate_account_number(
    imperfect_tables: dict,
    tmp_path: Path,
) -> None:
    validator = FinanceDuckDBFKValidator.for_profile("dev")
    malformed = _copy_tables(imperfect_tables)
    malformed["accounts"].loc[1, "account_number"] = malformed["accounts"].loc[
        0, "account_number"
    ]

    checks = _results_by_name(
        validator._validate_csv_paths(_export_tables(validator, malformed, tmp_path))
    )

    assert not checks["accounts.duckdb_load"].passed


def test_invalid_canonical_ddl_is_reported(
    imperfect_tables: dict,
    tmp_path: Path,
) -> None:
    validator = FinanceDuckDBFKValidator.for_profile("dev")
    ddl_path = tmp_path / "invalid.sql"
    ddl_path.write_text("CREATE TABLE broken (", encoding="utf-8")
    validator.settings = replace(validator.settings, schema_source=ddl_path)
    csv_dir = tmp_path / "csv"

    results = validator._validate_csv_paths(
        _export_tables(validator, imperfect_tables, csv_dir)
    )

    assert len(results) == 1
    assert not results[0].passed
    assert results[0].check_name == "schema.duckdb_ddl"


def test_exported_validation_reports_missing_csvs(tmp_path: Path) -> None:
    validator = FinanceDuckDBFKValidator.for_profile("full")

    with pytest.raises(FileNotFoundError, match="accounts.csv"):
        validator._csv_paths(tmp_path)


def test_validator_rejects_non_finance_settings() -> None:
    settings = GenerationSettings.from_config_files("sales", "dev")
    with pytest.raises(ValueError, match="only supports finance"):
        FinanceDuckDBFKValidator(DeterministicGenerator(settings))


def _copy_tables(tables: dict) -> dict:
    return {name: table.copy(deep=True) for name, table in tables.items()}


def _export_tables(
    validator: FinanceDuckDBFKValidator,
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
