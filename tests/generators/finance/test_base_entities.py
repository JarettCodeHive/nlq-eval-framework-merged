from __future__ import annotations

from datetime import date
from datetime import datetime
from decimal import Decimal
import importlib.util
import inspect

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.finance.config import settings_for_profile
from generators.finance.generator import FINANCE_COLUMN_CONTRACTS
from generators.finance.generator import FinanceBaseEntityGenerator


def _dependencies_available() -> bool:
    return all(
        importlib.util.find_spec(package) is not None for package in ("numpy", "pandas")
    )


pytestmark = pytest.mark.skipif(
    not _dependencies_available(),
    reason="numpy and pandas are required",
)


@pytest.fixture(scope="module")
def dev_generation() -> tuple[FinanceBaseEntityGenerator, dict[str, object]]:
    generator = FinanceBaseEntityGenerator.for_profile("dev")
    return generator, generator.generate_tables()


def test_generate_dev_base_tables_with_configured_shape(
    dev_generation: tuple[FinanceBaseEntityGenerator, dict[str, object]],
) -> None:
    generator, tables = dev_generation

    assert list(tables) == list(FINANCE_COLUMN_CONTRACTS)
    for table_name, columns in FINANCE_COLUMN_CONTRACTS.items():
        table = tables[table_name]
        assert table.columns.tolist() == columns
        assert len(table) == generator.generator.row_count(table_name)


def test_accounts_follow_chart_of_accounts_contract(
    dev_generation: tuple[FinanceBaseEntityGenerator, dict[str, object]],
) -> None:
    generator, tables = dev_generation
    accounts = tables["accounts"]
    mappings = generator.finance_config["business_mappings"]
    currencies = generator.finance_config["domain_values"]["source_currencies"]

    assert accounts["account_id"].is_unique
    assert accounts["account_number"].is_unique
    assert accounts["parent_account_id"].eq("").any()
    assert accounts["parent_account_id"].ne("").any()
    account_lookup = accounts.set_index("account_id")
    for row in accounts.itertuples(index=False):
        assert row.account_subtype in mappings["account_subtypes"][row.account_type]
        assert (
            row.normal_balance
            == mappings["normal_balance_by_account_type"][row.account_type]
        )
        if row.parent_account_id != "":
            parent = account_lookup.loc[int(row.parent_account_id)]
            assert int(row.parent_account_id) < row.account_id
            assert parent["account_type"] == row.account_type

    active = accounts[accounts["is_active"]]
    active_counts = active.groupby("currency_code").size().to_dict()
    assert all(active_counts.get(currency, 0) >= 2 for currency in currencies)


def test_fx_rates_form_complete_positive_synthetic_grid(
    dev_generation: tuple[FinanceBaseEntityGenerator, dict[str, object]],
) -> None:
    generator, tables = dev_generation
    fx_rates = tables["fx_rates"]
    calendar = generator.generation_rules["fx_calendar"]
    boundaries = set(generator.base_config["imperfections"]["boundary_dates"])

    assert not fx_rates.duplicated(["from_currency", "to_currency", "rate_date"]).any()
    assert set(fx_rates["rate_date"]).issuperset(boundaries)
    assert set(fx_rates["to_currency"]) == {"USD"}
    assert (
        fx_rates.groupby(["from_currency", "to_currency"])
        .size()
        .eq(calendar["expected_total_dates"]["dev"])
        .all()
    )
    assert fx_rates["rate"].ne("").all()
    assert all(Decimal(value) > 0 for value in fx_rates["rate"])
    assert all(len(value.rsplit(".", 1)[1]) == 6 for value in fx_rates["rate"])
    identity = fx_rates[fx_rates["from_currency"] == "USD"]
    assert set(identity["rate"]) == {"1.000000"}


def test_transactions_have_valid_fx_keys_amounts_and_timestamps(
    dev_generation: tuple[FinanceBaseEntityGenerator, dict[str, object]],
) -> None:
    generator, tables = dev_generation
    transactions = tables["transactions"]
    fx_rates = tables["fx_rates"]
    boundaries = set(generator.base_config["imperfections"]["boundary_dates"])

    joined = transactions.merge(
        fx_rates,
        left_on=["source_currency", "target_currency", "transaction_date"],
        right_on=["from_currency", "to_currency", "rate_date"],
        how="left",
        validate="many_to_one",
    )
    assert joined["rate_id"].notna().all()
    assert set(transactions["transaction_date"]).isdisjoint(boundaries)
    assert set(transactions["source_system"]) == set(
        generator.finance_config["domain_values"]["source_systems"]
    )
    assert set(transactions["target_currency"]) == {"USD"}
    assert all(Decimal(value) > 0 for value in transactions["total_amount"])
    assert all(
        len(value.rsplit(".", 1)[1]) == 4 for value in transactions["total_amount"]
    )
    for row in transactions.itertuples(index=False):
        assert datetime.fromisoformat(row.posted_at).date() >= date.fromisoformat(
            row.transaction_date
        )


def test_ledger_entries_are_exact_balanced_and_preserve_left_join_gap(
    dev_generation: tuple[FinanceBaseEntityGenerator, dict[str, object]],
) -> None:
    generator, tables = dev_generation
    transactions = tables["transactions"]
    ledger = tables["ledger_entries"]

    posted_ids = set(ledger["transaction_id"])
    transaction_ids = set(transactions["transaction_id"])
    expected_unposted = round(
        len(transactions)
        * generator.generation_rules["transactions"]["unposted_to_ledger_fraction"]
    )
    assert len(transaction_ids - posted_ids) == expected_unposted
    assert posted_ids.issubset(transaction_ids)
    assert set(ledger["account_id"]).issubset(set(tables["accounts"]["account_id"]))
    assert not ledger.duplicated(["transaction_id", "line_number"]).any()

    transaction_lookup = transactions.set_index("transaction_id")
    for transaction_id, lines in ledger.groupby("transaction_id", sort=False):
        assert lines["line_number"].tolist() == list(range(1, len(lines) + 1))
        assert len(lines) % 2 == 0
        debit_total = sum(
            Decimal(value) for value in lines["debit_amount"] if value != ""
        )
        credit_total = sum(
            Decimal(value) for value in lines["credit_amount"] if value != ""
        )
        transaction = transaction_lookup.loc[transaction_id]
        assert debit_total == credit_total == Decimal(transaction["total_amount"])
        assert set(lines["currency_code"]) == {transaction["source_currency"]}
        assert all(
            datetime.fromisoformat(value)
            >= datetime.fromisoformat(transaction["posted_at"])
            for value in lines["created_at"]
        )

    populated_sides = ledger[["debit_amount", "credit_amount"]].ne("").sum(axis=1)
    assert populated_sides.eq(1).all()


def test_ledger_entries_exercise_transaction_account_many_to_many(
    dev_generation: tuple[FinanceBaseEntityGenerator, dict[str, object]],
) -> None:
    _, tables = dev_generation
    ledger = tables["ledger_entries"]

    assert ledger.groupby("transaction_id")["account_id"].nunique().max() >= 2
    assert ledger.groupby("account_id")["transaction_id"].nunique().max() >= 2


def test_budgets_use_valid_quarterly_usd_periods(
    dev_generation: tuple[FinanceBaseEntityGenerator, dict[str, object]],
) -> None:
    generator, tables = dev_generation
    budgets = tables["budgets"]
    account_ids = set(tables["accounts"]["account_id"])

    assert set(budgets["account_id"]).issubset(account_ids)
    assert set(budgets["currency_code"]) == {"USD"}
    assert set(budgets["scenario"]) == set(
        generator.finance_config["domain_values"]["budget_scenarios"]
    )
    assert all(Decimal(value) > 0 for value in budgets["budget_amount"])
    assert all(len(value.rsplit(".", 1)[1]) == 4 for value in budgets["budget_amount"])
    assert not budgets.duplicated(
        ["account_id", "fiscal_year", "period_start", "period_end", "scenario"]
    ).any()
    for _, periods in budgets.sort_values(["account_id", "period_start"]).groupby(
        "account_id"
    ):
        previous_end: date | None = None
        for row in periods.itertuples(index=False):
            start = date.fromisoformat(row.period_start)
            end = date.fromisoformat(row.period_end)
            assert start < end
            assert datetime.fromisoformat(row.created_at).date() < start
            if previous_end is not None:
                assert start >= previous_end
            previous_end = end

    actual_dates = tables["ledger_entries"][["account_id", "transaction_id"]].merge(
        tables["transactions"][["transaction_id", "transaction_date"]],
        on="transaction_id",
    )
    matched = []
    for row in budgets.itertuples(index=False):
        rows = actual_dates[
            (actual_dates["account_id"] == row.account_id)
            & (actual_dates["transaction_date"] >= row.period_start)
            & (actual_dates["transaction_date"] < row.period_end)
        ]
        matched.append(not rows.empty)
    assert any(matched)
    assert not all(matched)


def test_finance_base_generation_is_reproducible() -> None:
    settings = settings_for_profile("dev")
    first = FinanceBaseEntityGenerator(
        DeterministicGenerator(settings)
    ).generate_tables()
    second = FinanceBaseEntityGenerator(
        DeterministicGenerator(settings)
    ).generate_tables()

    pd = pytest.importorskip("pandas")
    for table_name in FINANCE_COLUMN_CONTRACTS:
        pd.testing.assert_frame_equal(first[table_name], second[table_name])


def test_unrelated_named_streams_do_not_change_finance_generation() -> None:
    settings = settings_for_profile("dev")
    baseline = FinanceBaseEntityGenerator(
        DeterministicGenerator(settings)
    ).generate_tables()

    isolated_generator = DeterministicGenerator(settings)
    isolated_generator.rng_for("finance:test:unrelated").random(100)
    generated = FinanceBaseEntityGenerator(isolated_generator).generate_tables()

    pd = pytest.importorskip("pandas")
    for table_name in FINANCE_COLUMN_CONTRACTS:
        pd.testing.assert_frame_equal(baseline[table_name], generated[table_name])


def test_generator_rejects_non_finance_settings() -> None:
    with pytest.raises(ValueError, match="only supports the finance domain"):
        FinanceBaseEntityGenerator(
            DeterministicGenerator(GenerationSettings.from_config_files("sales", "dev"))
        )


def test_generation_uses_no_wall_clock_or_external_dataset_inputs() -> None:
    source = inspect.getsource(
        __import__("generators.finance.generator", fromlist=["*"])
    )

    for forbidden_call in (
        "date.today(",
        "datetime.now(",
        "datetime.utcnow(",
        "pd.read_csv(",
        "pd.read_sql(",
    ):
        assert forbidden_call not in source
