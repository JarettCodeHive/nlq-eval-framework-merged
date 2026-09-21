from __future__ import annotations

from datetime import date
from datetime import datetime
from decimal import Decimal
import importlib.util
import inspect

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.finance.distributions import FinanceDistributionApplier
from generators.finance.generator import FINANCE_COLUMN_CONTRACTS
from generators.finance.generator import FinanceBaseEntityGenerator
from generators.finance.validators.generated_tables import (
    validate_finance_generated_tables,
)


pytestmark = pytest.mark.skipif(
    not all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    ),
    reason="numpy, pandas, and Faker are not installed",
)


@pytest.fixture(scope="module")
def base_and_distributed() -> tuple[dict, dict, FinanceDistributionApplier]:
    base = FinanceBaseEntityGenerator.for_profile("dev").generate_tables()
    snapshot = {name: table.copy(deep=True) for name, table in base.items()}
    applier = FinanceDistributionApplier.for_profile("dev")
    distributed = applier.apply_to_tables(base)

    assert all(base[name].equals(snapshot[name]) for name in base)
    return base, distributed, applier


def test_finance_distributions_preserve_contract_and_relational_guards(
    base_and_distributed: tuple[dict, dict, FinanceDistributionApplier],
) -> None:
    base, distributed, applier = base_and_distributed

    assert list(distributed) == list(FINANCE_COLUMN_CONTRACTS)
    for table_name, columns in FINANCE_COLUMN_CONTRACTS.items():
        assert distributed[table_name].columns.tolist() == columns
        assert len(distributed[table_name]) == len(base[table_name])
    validate_finance_generated_tables(applier.generator, distributed)


@pytest.mark.parametrize(
    ("table_name", "column_name", "settings_key"),
    [
        ("transactions", "total_amount", "transaction_amount"),
        ("budgets", "budget_amount", "budget_amount"),
    ],
)
def test_pareto_amounts_are_bounded_and_fixed_scale(
    base_and_distributed: tuple[dict, dict, FinanceDistributionApplier],
    table_name: str,
    column_name: str,
    settings_key: str,
) -> None:
    base, distributed, applier = base_and_distributed
    spec = applier.settings.distributions[settings_key]
    values = distributed[table_name][column_name]

    assert not values.equals(base[table_name][column_name])
    assert (
        values.map(Decimal)
        .between(Decimal(spec["min_amount"]), Decimal(spec["max_amount"]))
        .all()
    )
    assert values.map(lambda value: Decimal(value).as_tuple().exponent == -4).all()


def test_poisson_posting_frequency_preserves_exact_ledger_balance(
    base_and_distributed: tuple[dict, dict, FinanceDistributionApplier],
) -> None:
    base, distributed, _ = base_and_distributed
    source_posted = set(base["ledger_entries"]["transaction_id"])
    ledger = distributed["ledger_entries"]
    line_counts = ledger.groupby("transaction_id").size()
    pair_counts = line_counts // 2

    assert len(ledger) == len(base["ledger_entries"])
    assert set(ledger["transaction_id"]) == source_posted
    assert (line_counts % 2 == 0).all()
    assert pair_counts.nunique() > 1

    amounts = distributed["transactions"].set_index("transaction_id")["total_amount"]
    for transaction_id, group in ledger.groupby("transaction_id", sort=False):
        debits = sum(
            (Decimal(value) for value in group["debit_amount"] if value != ""),
            Decimal("0"),
        )
        credits = sum(
            (Decimal(value) for value in group["credit_amount"] if value != ""),
            Decimal("0"),
        )
        assert debits == credits == Decimal(amounts.loc[transaction_id])


def test_clustered_dates_preserve_calendar_keys_and_chronology(
    base_and_distributed: tuple[dict, dict, FinanceDistributionApplier],
) -> None:
    base, distributed, applier = base_and_distributed
    assert not base["accounts"]["created_at"].equals(
        distributed["accounts"]["created_at"]
    )
    assert not base["transactions"]["transaction_date"].equals(
        distributed["transactions"]["transaction_date"]
    )
    assert base["budgets"]["period_start"].equals(
        distributed["budgets"]["period_start"]
    )
    assert base["budgets"]["period_end"].equals(
        distributed["budgets"]["period_end"]
    )
    assert base["fx_rates"]["rate_date"].equals(
        distributed["fx_rates"]["rate_date"]
    )

    fx_keys = set(
        zip(
            distributed["fx_rates"]["rate_date"],
            distributed["fx_rates"]["from_currency"],
            strict=True,
        )
    )
    maximum_lag = int(
        applier.generation_rules["transactions"]["posted_lag_days"]["maximum"]
    )
    for row in distributed["transactions"].itertuples(index=False):
        transaction_date = date.fromisoformat(row.transaction_date)
        posted_at = datetime.fromisoformat(row.posted_at)
        assert (row.transaction_date, row.source_currency) in fx_keys
        assert transaction_date <= posted_at.date()
        assert (posted_at.date() - transaction_date).days <= maximum_lag

    posted_at = {
        int(row.transaction_id): datetime.fromisoformat(row.posted_at)
        for row in distributed["transactions"].itertuples(index=False)
    }
    for row in distributed["ledger_entries"].itertuples(index=False):
        assert datetime.fromisoformat(row.created_at) >= posted_at[row.transaction_id]

    for row in distributed["budgets"].itertuples(index=False):
        assert datetime.fromisoformat(row.created_at).date() < date.fromisoformat(
            row.period_start
        )
    for row in distributed["fx_rates"].itertuples(index=False):
        assert datetime.fromisoformat(row.created_at).date() == date.fromisoformat(
            row.rate_date
        )


def test_fx_movement_changes_non_identity_rates_and_preserves_scale(
    base_and_distributed: tuple[dict, dict, FinanceDistributionApplier],
) -> None:
    base, distributed, _ = base_and_distributed
    identity = distributed["fx_rates"]["from_currency"] == "USD"
    non_identity = ~identity

    assert not base["fx_rates"].loc[non_identity, "rate"].equals(
        distributed["fx_rates"].loc[non_identity, "rate"]
    )
    assert (distributed["fx_rates"].loc[identity, "rate"] == "1.000000").all()
    assert distributed["fx_rates"]["rate"].map(Decimal).gt(0).all()
    assert distributed["fx_rates"]["rate"].map(
        lambda value: Decimal(value).as_tuple().exponent == -6
    ).all()


def test_finance_distributed_generation_is_reproducible() -> None:
    first = FinanceDistributionApplier.for_profile("dev").generate_distributed_tables()
    second = FinanceDistributionApplier.for_profile("dev").generate_distributed_tables()

    for table_name in FINANCE_COLUMN_CONTRACTS:
        assert first[table_name].equals(second[table_name])


def test_distribution_applier_rejects_non_finance_settings() -> None:
    settings = GenerationSettings.from_config_files("crm", "dev")
    with pytest.raises(ValueError, match="only supports finance"):
        FinanceDistributionApplier(DeterministicGenerator(settings))


def test_distribution_stage_does_not_read_written_datasets() -> None:
    source = inspect.getsource(
        __import__("generators.finance.distributions", fromlist=["*"])
    )
    assert "read_csv(" not in source
    assert "read_sql(" not in source
