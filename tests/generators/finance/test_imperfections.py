from __future__ import annotations

from datetime import date
from datetime import datetime
from decimal import Decimal
import importlib.util

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.core.imperfections import count_from_pct
from generators.finance.distributions import FinanceDistributionApplier
from generators.finance.generator import FINANCE_COLUMN_CONTRACTS
from generators.finance.imperfections import FinanceImperfectionInjector


pytestmark = pytest.mark.skipif(
    not all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    ),
    reason="numpy, pandas, and Faker are not installed",
)


@pytest.fixture(scope="module")
def distributed_and_imperfect() -> tuple[FinanceImperfectionInjector, dict, dict]:
    injector = FinanceImperfectionInjector.for_profile("dev")
    distributed = FinanceDistributionApplier(
        injector.generator
    ).generate_distributed_tables()
    snapshot = {name: table.copy(deep=True) for name, table in distributed.items()}
    imperfect = injector.apply_to_tables(distributed)

    assert all(distributed[name].equals(snapshot[name]) for name in distributed)
    return injector, distributed, imperfect


def test_imperfections_preserve_schema_and_expected_row_counts(
    distributed_and_imperfect: tuple[FinanceImperfectionInjector, dict, dict],
) -> None:
    injector, distributed, imperfect = distributed_and_imperfect
    expected_duplicates = count_from_pct(
        len(distributed["budgets"]),
        float(injector.config["duplicate_pct"]),
    )

    for table_name, columns in FINANCE_COLUMN_CONTRACTS.items():
        assert imperfect[table_name].columns.tolist() == columns
        expected_rows = len(distributed[table_name])
        if table_name == "budgets":
            expected_rows += expected_duplicates
        assert len(imperfect[table_name]) == expected_rows
    assert imperfect["budgets"]["budget_id"].is_unique


def test_near_duplicate_budgets_keep_business_key_and_vary_values(
    distributed_and_imperfect: tuple[FinanceImperfectionInjector, dict, dict],
) -> None:
    injector, distributed, imperfect = distributed_and_imperfect
    duplicate_count = count_from_pct(
        len(distributed["budgets"]),
        float(injector.config["duplicate_pct"]),
    )
    target = injector.finance_config["imperfection_targets"][
        "near_duplicate_budgets"
    ]
    duplicates = imperfect["budgets"].tail(duplicate_count)
    source = distributed["budgets"].set_index(target["business_key_fields"])

    assert duplicates["budget_id"].min() > distributed["budgets"]["budget_id"].max()
    for row in duplicates.itertuples(index=False):
        key = tuple(getattr(row, field) for field in target["business_key_fields"])
        original = source.loc[key]
        assert row.budget_amount != original["budget_amount"]
        assert row.created_at != original["created_at"]
        assert datetime.fromisoformat(row.created_at).date() < date.fromisoformat(
            row.period_start
        )


def test_transaction_outliers_rebuild_exact_balanced_ledger(
    distributed_and_imperfect: tuple[FinanceImperfectionInjector, dict, dict],
) -> None:
    injector, _, imperfect = distributed_and_imperfect
    target = injector.finance_config["imperfection_targets"][
        "transaction_amount_outliers"
    ]
    minimum = Decimal(target["minimum_value"])
    maximum = Decimal(target["maximum_value"])
    transactions = imperfect["transactions"]
    outliers = transactions[transactions["total_amount"].map(Decimal) >= minimum]
    expected = count_from_pct(
        len(transactions),
        float(injector.config["outlier_pct"]),
    )

    assert len(outliers) == expected
    assert outliers["total_amount"].map(Decimal).between(minimum, maximum).all()
    assert outliers["total_amount"].map(
        lambda value: Decimal(value).as_tuple().exponent == -4
    ).all()
    for row in outliers.itertuples(index=False):
        lines = imperfect["ledger_entries"][
            imperfect["ledger_entries"]["transaction_id"] == row.transaction_id
        ]
        assert not lines.empty
        debits = sum(
            (Decimal(value) for value in lines["debit_amount"] if value != ""),
            Decimal("0"),
        )
        credits = sum(
            (Decimal(value) for value in lines["credit_amount"] if value != ""),
            Decimal("0"),
        )
        assert debits == credits == Decimal(row.total_amount)


def test_boundary_transactions_keep_fx_coverage_and_valid_timestamps(
    distributed_and_imperfect: tuple[FinanceImperfectionInjector, dict, dict],
) -> None:
    injector, _, imperfect = distributed_and_imperfect
    boundaries = set(injector.config["boundary_dates"])
    boundary_rows = imperfect["transactions"][
        imperfect["transactions"]["transaction_date"].isin(boundaries)
    ]
    fx_lookup = imperfect["fx_rates"].set_index(
        ["from_currency", "to_currency", "rate_date"]
    )
    ledger_lag = injector.generation_rules["ledger_entries"]["created_lag_hours"]
    maximum_seconds = int(ledger_lag["maximum"]) * 60 * 60

    assert set(boundary_rows["transaction_date"]) == boundaries
    for row in boundary_rows.itertuples(index=False):
        rate = fx_lookup.loc[
            (row.source_currency, row.target_currency, row.transaction_date),
            "rate",
        ]
        assert rate != ""
        transaction_date = date.fromisoformat(row.transaction_date)
        posted = datetime.fromisoformat(row.posted_at)
        assert posted.date() >= transaction_date
        lines = imperfect["ledger_entries"][
            imperfect["ledger_entries"]["transaction_id"] == row.transaction_id
        ]
        assert not lines.empty
        for created_at in lines["created_at"]:
            lag = (datetime.fromisoformat(created_at) - posted).total_seconds()
            assert 0 <= lag <= maximum_seconds


def test_missing_fx_rates_match_rate_and_protect_required_rows(
    distributed_and_imperfect: tuple[FinanceImperfectionInjector, dict, dict],
) -> None:
    injector, distributed, imperfect = distributed_and_imperfect
    fx_rates = imperfect["fx_rates"]
    expected = count_from_pct(
        len(distributed["fx_rates"]),
        float(injector.config["null_pct"]),
    )
    missing = fx_rates[fx_rates["rate"] == ""]

    assert len(missing) == expected
    assert not (missing["from_currency"] == "USD").any()
    assert not missing["rate_date"].isin(injector.config["boundary_dates"]).any()

    missing_keys = set(
        missing[["from_currency", "to_currency", "rate_date"]].itertuples(
            index=False,
            name=None,
        )
    )
    transaction_keys = set(
        imperfect["transactions"][
            ["source_currency", "target_currency", "transaction_date"]
        ].itertuples(index=False, name=None)
    )
    assert missing_keys & transaction_keys


def test_physical_relationships_and_membership_remain_valid(
    distributed_and_imperfect: tuple[FinanceImperfectionInjector, dict, dict],
) -> None:
    _, distributed, imperfect = distributed_and_imperfect

    assert set(imperfect["ledger_entries"]["transaction_id"]).issubset(
        set(imperfect["transactions"]["transaction_id"])
    )
    assert set(imperfect["ledger_entries"]["account_id"]).issubset(
        set(imperfect["accounts"]["account_id"])
    )
    assert set(imperfect["budgets"]["account_id"]).issubset(
        set(imperfect["accounts"]["account_id"])
    )
    assert set(imperfect["ledger_entries"]["transaction_id"]) == set(
        distributed["ledger_entries"]["transaction_id"]
    )


def test_imperfect_generation_is_reproducible() -> None:
    first = FinanceImperfectionInjector.for_profile("dev").generate_imperfect_tables()
    second = FinanceImperfectionInjector.for_profile("dev").generate_imperfect_tables()

    for table_name in FINANCE_COLUMN_CONTRACTS:
        assert first[table_name].equals(second[table_name])


def test_imperfection_preflight_rejects_invalid_distributed_input(
    distributed_and_imperfect: tuple[FinanceImperfectionInjector, dict, dict],
) -> None:
    injector, distributed, _ = distributed_and_imperfect
    malformed = {name: table.copy(deep=True) for name, table in distributed.items()}
    malformed["budgets"].at[0, "budget_amount"] = "25000000.0001"

    with pytest.raises(ValueError, match="outside configured distribution bounds"):
        injector.apply_to_tables(malformed)


def test_injector_rejects_non_finance_settings() -> None:
    settings = GenerationSettings.from_config_files("sales", "dev")
    with pytest.raises(ValueError, match="only supports finance"):
        FinanceImperfectionInjector(DeterministicGenerator(settings))
