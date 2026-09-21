from __future__ import annotations

from datetime import datetime
from datetime import timedelta
import importlib.util

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.finance.distributions import FinanceDistributionApplier
from generators.finance.generator import FinanceBaseEntityGenerator
from generators.finance.validators.distributed_tables import (
    FinanceDistributedTablesValidator,
)
from generators.finance.validators.distributed_tables import (
    validate_finance_distributed_tables,
)


pytestmark = pytest.mark.skipif(
    not all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    ),
    reason="numpy, pandas, and Faker are not installed",
)


@pytest.fixture(scope="module")
def validated_tables() -> tuple[FinanceDistributionApplier, dict, dict]:
    applier = FinanceDistributionApplier.for_profile("dev")
    base = FinanceBaseEntityGenerator(applier.generator).generate_tables()
    distributed = applier.apply_to_tables(base)
    return applier, base, distributed


def test_valid_distributed_tables_pass_with_and_without_source(
    validated_tables: tuple[FinanceDistributionApplier, dict, dict],
) -> None:
    applier, base, distributed = validated_tables

    validate_finance_distributed_tables(applier.generator, distributed)
    validate_finance_distributed_tables(
        applier.generator,
        distributed,
        source_tables=base,
    )


def test_rejects_amount_outside_effective_pareto_bounds(
    validated_tables: tuple[FinanceDistributionApplier, dict, dict],
) -> None:
    applier, _, distributed = validated_tables
    malformed = _copy_tables(distributed)
    malformed["budgets"].at[0, "budget_amount"] = "25000000.0001"

    with pytest.raises(ValueError, match="outside configured distribution bounds"):
        validate_finance_distributed_tables(applier.generator, malformed)


def test_rejects_date_outside_configured_distribution_window(
    validated_tables: tuple[FinanceDistributionApplier, dict, dict],
) -> None:
    applier, _, distributed = validated_tables
    malformed = _copy_tables(distributed)
    malformed["accounts"].at[0, "created_at"] = "2021-12-31T23:59:59"

    with pytest.raises(ValueError, match="outside its configured distribution window"):
        validate_finance_distributed_tables(applier.generator, malformed)


def test_rejects_ledger_timestamp_outside_configured_lag(
    validated_tables: tuple[FinanceDistributionApplier, dict, dict],
) -> None:
    applier, _, distributed = validated_tables
    malformed = _copy_tables(distributed)
    transaction_id = int(malformed["ledger_entries"].at[0, "transaction_id"])
    posted = malformed["transactions"].set_index("transaction_id").loc[
        transaction_id, "posted_at"
    ]
    malformed["ledger_entries"].at[0, "created_at"] = (
        datetime.fromisoformat(posted) + timedelta(hours=49)
    ).strftime("%Y-%m-%dT%H:%M:%S")

    with pytest.raises(ValueError, match="outside its configured lag window"):
        validate_finance_distributed_tables(applier.generator, malformed)


def test_rejects_fx_rate_outside_bounded_movement(
    validated_tables: tuple[FinanceDistributionApplier, dict, dict],
) -> None:
    applier, _, distributed = validated_tables
    malformed = _copy_tables(distributed)
    position = malformed["fx_rates"].index[
        malformed["fx_rates"]["from_currency"] == "EUR"
    ][0]
    malformed["fx_rates"].at[position, "rate"] = "9.000000"

    with pytest.raises(ValueError, match="exceeds configured bounded movement"):
        validate_finance_distributed_tables(applier.generator, malformed)


def test_rejects_primary_key_drift_from_source(
    validated_tables: tuple[FinanceDistributionApplier, dict, dict],
) -> None:
    applier, base, distributed = validated_tables
    malformed = _copy_tables(distributed)
    malformed["fx_rates"].at[0, "rate_id"] = 999999

    with pytest.raises(ValueError, match="fx_rates.rate_id changed"):
        validate_finance_distributed_tables(
            applier.generator,
            malformed,
            source_tables=base,
        )


def test_rejects_unapproved_field_drift_from_source(
    validated_tables: tuple[FinanceDistributionApplier, dict, dict],
) -> None:
    applier, base, distributed = validated_tables
    malformed = _copy_tables(distributed)
    malformed["accounts"].at[0, "account_name"] = "Unexpected Replacement"

    with pytest.raises(ValueError, match="accounts.account_name changed unexpectedly"):
        validate_finance_distributed_tables(
            applier.generator,
            malformed,
            source_tables=base,
        )


def test_rejects_posted_transaction_membership_drift_from_source(
    validated_tables: tuple[FinanceDistributionApplier, dict, dict],
) -> None:
    applier, base, distributed = validated_tables
    changed_source = _copy_tables(base)
    source_ledger = changed_source["ledger_entries"]
    posted_ids = set(source_ledger["transaction_id"])
    replacement_id = next(
        value
        for value in changed_source["transactions"]["transaction_id"]
        if value not in posted_ids
    )
    replaced_id = int(source_ledger.at[0, "transaction_id"])
    source_ledger.loc[
        source_ledger["transaction_id"] == replaced_id,
        "transaction_id",
    ] = replacement_id

    with pytest.raises(ValueError, match="Posted transaction membership changed"):
        validate_finance_distributed_tables(
            applier.generator,
            distributed,
            source_tables=changed_source,
        )


def test_validator_rejects_non_finance_settings() -> None:
    settings = GenerationSettings.from_config_files("sales", "dev")
    with pytest.raises(ValueError, match="only supports the finance domain"):
        FinanceDistributedTablesValidator(DeterministicGenerator(settings))


def _copy_tables(tables: dict) -> dict:
    return {name: table.copy(deep=True) for name, table in tables.items()}
