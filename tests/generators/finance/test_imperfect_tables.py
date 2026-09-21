from __future__ import annotations

from decimal import Decimal
import importlib.util

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.finance.distributions import FinanceDistributionApplier
from generators.finance.imperfections import FinanceImperfectionInjector
from generators.finance.validators.imperfect_tables import (
    FinanceImperfectTablesValidator,
)
from generators.finance.validators.imperfect_tables import (
    validate_finance_imperfect_tables,
)


pytestmark = pytest.mark.skipif(
    not all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    ),
    reason="numpy, pandas, and Faker are not installed",
)


@pytest.fixture(scope="module")
def validated_tables() -> tuple[FinanceImperfectionInjector, dict, dict]:
    injector = FinanceImperfectionInjector.for_profile("dev")
    distributed = FinanceDistributionApplier(
        injector.generator
    ).generate_distributed_tables()
    imperfect = injector.apply_to_tables(distributed)
    return injector, distributed, imperfect


def test_valid_imperfect_tables_pass_with_and_without_source(
    validated_tables: tuple[FinanceImperfectionInjector, dict, dict],
) -> None:
    injector, distributed, imperfect = validated_tables

    validate_finance_imperfect_tables(injector.generator, imperfect)
    validate_finance_imperfect_tables(
        injector.generator,
        imperfect,
        source_tables=distributed,
    )


def test_rejects_incorrect_missing_fx_rate_count(
    validated_tables: tuple[FinanceImperfectionInjector, dict, dict],
) -> None:
    injector, _, imperfect = validated_tables
    malformed = _copy_tables(imperfect)
    candidate = malformed["fx_rates"][
        malformed["fx_rates"]["rate"].ne("")
        & malformed["fx_rates"]["from_currency"].ne("USD")
        & ~malformed["fx_rates"]["rate_date"].isin(injector.config["boundary_dates"])
    ].index[0]
    malformed["fx_rates"].at[candidate, "rate"] = ""

    with pytest.raises(ValueError, match="NULL count differs"):
        validate_finance_imperfect_tables(injector.generator, malformed)


def test_rejects_missing_identity_rate_even_when_null_count_is_unchanged(
    validated_tables: tuple[FinanceImperfectionInjector, dict, dict],
) -> None:
    injector, distributed, imperfect = validated_tables
    malformed = _copy_tables(imperfect)
    existing_missing = malformed["fx_rates"].index[
        malformed["fx_rates"]["rate"].eq("")
    ][0]
    identity = malformed["fx_rates"].index[
        malformed["fx_rates"]["from_currency"].eq("USD")
    ][0]
    malformed["fx_rates"].at[existing_missing, "rate"] = distributed["fx_rates"].at[
        existing_missing, "rate"
    ]
    malformed["fx_rates"].at[identity, "rate"] = ""

    with pytest.raises(ValueError, match="identity FX rows contain missing"):
        validate_finance_imperfect_tables(injector.generator, malformed)


def test_rejects_byte_identical_near_duplicate_budget(
    validated_tables: tuple[FinanceImperfectionInjector, dict, dict],
) -> None:
    injector, _, imperfect = validated_tables
    malformed = _copy_tables(imperfect)
    base_count = injector.generator.row_count("budgets")
    duplicate = malformed["budgets"].loc[base_count]
    target = injector.finance_config["imperfection_targets"][
        "near_duplicate_budgets"
    ]
    source = (
        malformed["budgets"]
        .iloc[:base_count]
        .set_index(target["business_key_fields"])
        .loc[tuple(duplicate[field] for field in target["business_key_fields"])]
    )
    for field in target["variation_fields"]:
        malformed["budgets"].at[base_count, field] = source[field]

    with pytest.raises(ValueError, match="byte-identical"):
        validate_finance_imperfect_tables(injector.generator, malformed)


def test_rejects_duplicate_budget_orphan(
    validated_tables: tuple[FinanceImperfectionInjector, dict, dict],
) -> None:
    injector, _, imperfect = validated_tables
    malformed = _copy_tables(imperfect)
    malformed["budgets"].at[len(malformed["budgets"]) - 1, "account_id"] = 999999

    with pytest.raises(ValueError, match="budgets.account_id contains orphan"):
        validate_finance_imperfect_tables(injector.generator, malformed)


def test_rejects_outlier_ledger_imbalance(
    validated_tables: tuple[FinanceImperfectionInjector, dict, dict],
) -> None:
    injector, _, imperfect = validated_tables
    malformed = _copy_tables(imperfect)
    minimum = Decimal(
        injector.finance_config["imperfection_targets"][
            "transaction_amount_outliers"
        ]["minimum_value"]
    )
    outlier_id = int(
        malformed["transactions"].loc[
            malformed["transactions"]["total_amount"].map(Decimal) >= minimum,
            "transaction_id",
        ].iloc[0]
    )
    debit_position = malformed["ledger_entries"].index[
        (malformed["ledger_entries"]["transaction_id"] == outlier_id)
        & malformed["ledger_entries"]["debit_amount"].ne("")
    ][0]
    amount = Decimal(malformed["ledger_entries"].at[debit_position, "debit_amount"])
    malformed["ledger_entries"].at[debit_position, "debit_amount"] = (
        f"{amount + Decimal('0.0001'):.4f}"
    )

    with pytest.raises(ValueError, match="ledger totals differ"):
        validate_finance_imperfect_tables(injector.generator, malformed)


def test_rejects_missing_boundary_transaction(
    validated_tables: tuple[FinanceImperfectionInjector, dict, dict],
) -> None:
    injector, distributed, imperfect = validated_tables
    malformed = _copy_tables(imperfect)
    boundary = injector.config["boundary_dates"][0]
    position = malformed["transactions"].index[
        malformed["transactions"]["transaction_date"] == boundary
    ][0]
    transaction_id = malformed["transactions"].at[position, "transaction_id"]
    original = distributed["transactions"].set_index("transaction_id").loc[
        transaction_id
    ]
    malformed["transactions"].at[position, "transaction_date"] = original[
        "transaction_date"
    ]
    malformed["transactions"].at[position, "posted_at"] = original["posted_at"]

    with pytest.raises(ValueError, match="boundary dates are missing or repeated"):
        validate_finance_imperfect_tables(injector.generator, malformed)


def test_rejects_many_to_many_cardinality_damage(
    validated_tables: tuple[FinanceImperfectionInjector, dict, dict],
) -> None:
    injector, _, imperfect = validated_tables
    malformed = _copy_tables(imperfect)
    accounts_by_currency = {
        currency: int(group.iloc[0]["account_id"])
        for currency, group in malformed["accounts"].groupby("currency_code")
    }
    malformed["ledger_entries"]["account_id"] = [
        accounts_by_currency[currency]
        for currency in malformed["ledger_entries"]["currency_code"]
    ]

    with pytest.raises(ValueError, match="transaction using multiple accounts"):
        validate_finance_imperfect_tables(injector.generator, malformed)


def test_rejects_unapproved_source_row_drift(
    validated_tables: tuple[FinanceImperfectionInjector, dict, dict],
) -> None:
    injector, distributed, imperfect = validated_tables
    malformed = _copy_tables(imperfect)
    malformed["accounts"].at[0, "account_name"] = "Unexpected Replacement"

    with pytest.raises(ValueError, match="accounts.account_name changed unexpectedly"):
        validate_finance_imperfect_tables(
            injector.generator,
            malformed,
            source_tables=distributed,
        )


def test_validator_rejects_non_finance_settings() -> None:
    settings = GenerationSettings.from_config_files("sales", "dev")
    with pytest.raises(ValueError, match="only supports the finance domain"):
        FinanceImperfectTablesValidator(DeterministicGenerator(settings))


def _copy_tables(tables: dict) -> dict:
    return {name: table.copy(deep=True) for name, table in tables.items()}
