from __future__ import annotations

from decimal import Decimal
import importlib.util

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.finance.generator import FINANCE_COLUMN_CONTRACTS
from generators.finance.imperfections import FinanceImperfectionInjector
from generators.finance.validators.relational import FinanceRelationalValidator


pytestmark = pytest.mark.skipif(
    not all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    ),
    reason="numpy, pandas, and Faker are not installed",
)


@pytest.fixture(scope="module")
def imperfect_tables() -> dict:
    return FinanceImperfectionInjector.for_profile("dev").generate_imperfect_tables()


def test_generated_relational_validation_passes() -> None:
    validator = FinanceRelationalValidator.for_profile("dev")
    results = validator.generate_and_validate()
    checks = _results_by_name(results)

    assert validator.settings.table_order == tuple(FINANCE_COLUMN_CONTRACTS)
    assert results and all(result.passed for result in results)
    assert "ledger_entries.transaction_id.fk" in checks
    assert "ledger_entries.transaction_balance" in checks
    assert "ledger_entries.transaction_amount_alignment" in checks
    assert "ledger_entries.reversal_semantics" in checks
    assert "finance.transaction_account_many_to_many" in checks
    assert "transactions.without_ledger" in checks
    assert "budgets.with_actuals" in checks
    assert "budgets.without_actuals" in checks
    assert "fx_rates.transaction_lookup_coverage" in checks
    assert "fx_rates.missing_rate_path" in checks
    assert "finance.fx_conversion_examples" in checks
    assert "transactions.boundary_dates" in checks


def test_missing_table_reports_contract_failure_without_crashing(
    imperfect_tables: dict,
) -> None:
    malformed = _copy_tables(imperfect_tables)
    malformed.pop("fx_rates")
    checks = _results_by_name(
        FinanceRelationalValidator.for_profile("dev").validate_tables(malformed)
    )

    assert not checks["schema.table_order"].passed
    assert not checks["fx_rates.present"].passed


def test_wrong_column_order_stops_dependent_checks(imperfect_tables: dict) -> None:
    malformed = _copy_tables(imperfect_tables)
    columns = malformed["transactions"].columns.tolist()
    columns[0], columns[1] = columns[1], columns[0]
    malformed["transactions"] = malformed["transactions"][columns]
    checks = _results_by_name(
        FinanceRelationalValidator.for_profile("dev").validate_tables(malformed)
    )

    assert not checks["transactions.columns"].passed
    assert "transactions.row_count" not in checks


def test_key_fk_required_and_row_count_failures_are_reported(
    imperfect_tables: dict,
) -> None:
    malformed = _copy_tables(imperfect_tables)
    malformed["ledger_entries"].loc[1, "entry_id"] = malformed[
        "ledger_entries"
    ].loc[0, "entry_id"]
    malformed["ledger_entries"].loc[2, "account_id"] = 999999
    malformed["accounts"].loc[0, "account_name"] = ""
    malformed["fx_rates"] = malformed["fx_rates"].iloc[:-1]
    checks = _results_by_name(
        FinanceRelationalValidator.for_profile("dev").validate_tables(malformed)
    )

    assert not checks["ledger_entries.primary_key"].passed
    assert not checks["ledger_entries.account_id.fk"].passed
    assert not checks["accounts.account_name.not_null"].passed
    assert not checks["fx_rates.row_count"].passed


def test_account_hierarchy_and_mapping_failures_are_reported(
    imperfect_tables: dict,
) -> None:
    malformed = _copy_tables(imperfect_tables)
    malformed["accounts"].loc[0, "normal_balance"] = "Wrong"
    accounts = malformed["accounts"]
    child_position = accounts.index[accounts["parent_account_id"] != ""][0]
    child_id = int(accounts.loc[child_position, "account_id"])
    parent_id = int(accounts.loc[child_position, "parent_account_id"])
    accounts.loc[accounts["account_id"] == parent_id, "parent_account_id"] = child_id
    checks = _results_by_name(
        FinanceRelationalValidator.for_profile("dev").validate_tables(malformed)
    )

    assert not checks["accounts.normal_balance.mapping"].passed
    assert not checks["accounts.hierarchy_acyclic"].passed


def test_ledger_balance_reversal_and_side_failures_are_reported(
    imperfect_tables: dict,
) -> None:
    malformed = _copy_tables(imperfect_tables)
    debit_positions = malformed["ledger_entries"].index[
        malformed["ledger_entries"]["debit_amount"] != ""
    ]
    malformed["ledger_entries"].loc[debit_positions[0], "credit_amount"] = "1.0000"
    debit_position = debit_positions[1]
    amount = Decimal(malformed["ledger_entries"].loc[debit_position, "debit_amount"])
    malformed["ledger_entries"].loc[debit_position, "debit_amount"] = (
        f"{amount + Decimal('0.0001'):.4f}"
    )
    reversed_ids = set(
        malformed["transactions"].loc[
            malformed["transactions"]["reversed"], "transaction_id"
        ]
    )
    reversed_position = malformed["ledger_entries"].index[
        malformed["ledger_entries"]["transaction_id"].isin(reversed_ids)
    ][0]
    malformed["ledger_entries"].loc[reversed_position, "posting_type"] = "Standard"
    checks = _results_by_name(
        FinanceRelationalValidator.for_profile("dev").validate_tables(malformed)
    )

    assert not checks["ledger_entries.debit_credit_exclusivity"].passed
    assert not checks["ledger_entries.transaction_balance"].passed
    assert not checks["ledger_entries.transaction_amount_alignment"].passed
    assert not checks["ledger_entries.reversal_semantics"].passed


def test_many_to_many_and_unposted_failures_are_reported(
    imperfect_tables: dict,
) -> None:
    malformed = _copy_tables(imperfect_tables)
    accounts_by_currency = {
        currency: int(group.iloc[0]["account_id"])
        for currency, group in malformed["accounts"].groupby("currency_code")
    }
    malformed["ledger_entries"]["account_id"] = [
        accounts_by_currency[currency]
        for currency in malformed["ledger_entries"]["currency_code"]
    ]
    posted = set(malformed["ledger_entries"]["transaction_id"])
    unposted_id = next(
        value
        for value in malformed["transactions"]["transaction_id"]
        if value not in posted
    )
    malformed["ledger_entries"].loc[0, "transaction_id"] = unposted_id
    checks = _results_by_name(
        FinanceRelationalValidator.for_profile("dev").validate_tables(malformed)
    )

    assert not checks["finance.transaction_account_many_to_many"].passed
    assert not checks["transactions.without_ledger"].passed


def test_budget_and_fx_failures_are_reported(imperfect_tables: dict) -> None:
    malformed = _copy_tables(imperfect_tables)
    malformed["budgets"].loc[0, "currency_code"] = "EUR"
    malformed["budgets"].loc[1, "scenario"] = "Unknown"
    identity_position = malformed["fx_rates"].index[
        malformed["fx_rates"]["from_currency"] == "USD"
    ][0]
    malformed["fx_rates"].loc[identity_position, "rate"] = "1.000001"
    checks = _results_by_name(
        FinanceRelationalValidator.for_profile("dev").validate_tables(malformed)
    )

    assert not checks["budgets.currency_code"].passed
    assert not checks["budgets.scenario.domain"].passed
    assert not checks["fx_rates.identity"].passed


def test_boundary_and_chronology_failures_are_reported(
    imperfect_tables: dict,
) -> None:
    malformed = _copy_tables(imperfect_tables)
    boundary_position = malformed["transactions"].index[
        malformed["transactions"]["transaction_date"] == "1900-01-01"
    ][0]
    malformed["transactions"].loc[boundary_position, "transaction_date"] = (
        "2026-08-01"
    )
    malformed["transactions"].loc[0, "posted_at"] = "1900-01-01T00:00:00"
    malformed["ledger_entries"].loc[0, "created_at"] = "2099-01-01T00:00:00"
    checks = _results_by_name(
        FinanceRelationalValidator.for_profile("dev").validate_tables(malformed)
    )

    assert not checks["transactions.boundary_dates"].passed
    assert not checks["transactions.posted_at_temporal"].passed
    assert not checks["ledger_entries.lag_window"].passed


def test_validate_or_raise_combines_failures(imperfect_tables: dict) -> None:
    malformed = _copy_tables(imperfect_tables)
    malformed["accounts"].loc[0, "account_name"] = ""
    malformed["budgets"].loc[0, "currency_code"] = "EUR"

    with pytest.raises(ValueError, match="Integrity validation failed"):
        FinanceRelationalValidator.for_profile("dev").validate_or_raise(malformed)


def test_validator_rejects_non_finance_settings() -> None:
    settings = GenerationSettings.from_config_files("sales", "dev")
    with pytest.raises(ValueError, match="only supports finance"):
        FinanceRelationalValidator(DeterministicGenerator(settings))


def _copy_tables(tables: dict) -> dict:
    return {name: table.copy(deep=True) for name, table in tables.items()}


def _results_by_name(results: list) -> dict:
    return {result.check_name: result for result in results}
