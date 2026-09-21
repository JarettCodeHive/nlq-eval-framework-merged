from __future__ import annotations

import importlib.util

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.sales.distributions import SalesDistributionApplier
from generators.sales.imperfections import SalesImperfectionInjector
from generators.sales.validators.imperfect_tables import (
    SalesImperfectTablesValidator,
)
from generators.sales.validators.imperfect_tables import (
    validate_sales_imperfect_tables,
)


pytestmark = pytest.mark.skipif(
    not all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    ),
    reason="numpy, pandas, and Faker are not installed",
)


@pytest.fixture(scope="module")
def validated_tables() -> tuple[SalesImperfectionInjector, dict, dict]:
    injector = SalesImperfectionInjector.for_profile("dev")
    distributed = SalesDistributionApplier(
        injector.generator
    ).generate_distributed_tables()
    imperfect = injector.apply_to_tables(distributed)
    return injector, distributed, imperfect


def test_valid_imperfect_tables_pass_with_and_without_source(
    validated_tables: tuple[SalesImperfectionInjector, dict, dict],
) -> None:
    injector, distributed, imperfect = validated_tables

    validate_sales_imperfect_tables(injector.generator, imperfect)
    validate_sales_imperfect_tables(
        injector.generator,
        imperfect,
        source_tables=distributed,
    )


def test_rejects_incorrect_missing_price_count(
    validated_tables: tuple[SalesImperfectionInjector, dict, dict],
) -> None:
    injector, _, imperfect = validated_tables
    malformed = _copy_tables(imperfect)
    populated_position = int(
        malformed["products"].index[
            malformed["products"]["list_price"].astype(str).ne("")
        ][0]
    )
    malformed["products"].at[populated_position, "list_price"] = ""

    with pytest.raises(ValueError, match="NULL count differs"):
        validate_sales_imperfect_tables(injector.generator, malformed)


def test_rejects_byte_identical_near_duplicate(
    validated_tables: tuple[SalesImperfectionInjector, dict, dict],
) -> None:
    injector, _, imperfect = validated_tables
    malformed = _copy_tables(imperfect)
    base_count = injector.generator.row_count("quotations")
    duplicate_position = base_count
    duplicate = malformed["quotations"].loc[duplicate_position]
    source = (
        malformed["quotations"]
        .iloc[:base_count]
        .set_index(["deal_id", "product_id", "quote_number"])
        .loc[(duplicate.deal_id, duplicate.product_id, duplicate.quote_number)]
    )
    for field in ("quantity", "discount_pct", "quoted_at"):
        malformed["quotations"].at[duplicate_position, field] = source[field]

    with pytest.raises(ValueError, match="byte-identical"):
        validate_sales_imperfect_tables(injector.generator, malformed)


def test_rejects_duplicate_quotation_orphan(
    validated_tables: tuple[SalesImperfectionInjector, dict, dict],
) -> None:
    injector, _, imperfect = validated_tables
    malformed = _copy_tables(imperfect)
    malformed["quotations"].at[len(malformed["quotations"]) - 1, "product_id"] = 999999

    with pytest.raises(ValueError, match="contains orphan values"):
        validate_sales_imperfect_tables(injector.generator, malformed)


def test_rejects_missing_or_moved_boundary_value(
    validated_tables: tuple[SalesImperfectionInjector, dict, dict],
) -> None:
    injector, _, imperfect = validated_tables
    malformed = _copy_tables(imperfect)
    malformed["products"].at[0, "created_at"] = "2024-01-01T00:00:00"

    with pytest.raises(ValueError, match="boundary values are missing or moved"):
        validate_sales_imperfect_tables(injector.generator, malformed)


def test_rejects_unapproved_source_row_drift(
    validated_tables: tuple[SalesImperfectionInjector, dict, dict],
) -> None:
    injector, distributed, imperfect = validated_tables
    malformed = _copy_tables(imperfect)
    malformed["targets"].at[0, "territory"] = "Unexpected"

    with pytest.raises(ValueError, match="targets.territory changed unexpectedly"):
        validate_sales_imperfect_tables(
            injector.generator,
            malformed,
            source_tables=distributed,
        )


def test_validator_rejects_non_sales_settings() -> None:
    settings = GenerationSettings.from_config_files("crm", "dev")
    with pytest.raises(ValueError, match="only supports the sales domain"):
        SalesImperfectTablesValidator(DeterministicGenerator(settings))


def _copy_tables(tables: dict) -> dict:
    return {name: table.copy(deep=True) for name, table in tables.items()}
