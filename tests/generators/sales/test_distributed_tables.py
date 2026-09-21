from __future__ import annotations

import importlib.util

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.sales.distributions import SalesDistributionApplier
from generators.sales.generator import SalesBaseEntityGenerator
from generators.sales.validators.distributed_tables import (
    SalesDistributedTablesValidator,
)
from generators.sales.validators.distributed_tables import (
    validate_sales_distributed_tables,
)


pytestmark = pytest.mark.skipif(
    not all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    ),
    reason="numpy, pandas, and Faker are not installed",
)


@pytest.fixture(scope="module")
def validated_tables() -> tuple[SalesDistributionApplier, dict, dict]:
    applier = SalesDistributionApplier.for_profile("dev")
    base = SalesBaseEntityGenerator(applier.generator).generate_tables()
    distributed = applier.apply_to_tables(base)
    return applier, base, distributed


def test_valid_distributed_tables_pass_with_and_without_source(
    validated_tables: tuple[SalesDistributionApplier, dict, dict],
) -> None:
    applier, base, distributed = validated_tables

    validate_sales_distributed_tables(applier.generator, distributed)
    validate_sales_distributed_tables(
        applier.generator,
        distributed,
        source_tables=base,
    )


def test_rejects_amount_outside_effective_pareto_bounds(
    validated_tables: tuple[SalesDistributionApplier, dict, dict],
) -> None:
    applier, _, distributed = validated_tables
    malformed = _copy_tables(distributed)
    malformed["deals"].at[0, "deal_amount"] = "4999.00"

    with pytest.raises(ValueError, match="outside configured distribution bounds"):
        validate_sales_distributed_tables(applier.generator, malformed)


def test_rejects_amount_with_wrong_decimal_scale(
    validated_tables: tuple[SalesDistributionApplier, dict, dict],
) -> None:
    applier, _, distributed = validated_tables
    malformed = _copy_tables(distributed)
    malformed["targets"].at[0, "quota_amount"] = "100000.0"

    with pytest.raises(ValueError, match="does not use scale 2"):
        validate_sales_distributed_tables(applier.generator, malformed)


def test_rejects_date_outside_configured_distribution_window(
    validated_tables: tuple[SalesDistributionApplier, dict, dict],
) -> None:
    applier, _, distributed = validated_tables
    malformed = _copy_tables(distributed)
    unmatched = next(
        index
        for index, lead_id in enumerate(malformed["leads"]["lead_id"])
        if lead_id not in set(malformed["deals"]["lead_id"])
    )
    malformed["leads"].at[unmatched, "created_at"] = "2021-12-31T23:59:59"

    with pytest.raises(ValueError, match="outside its configured distribution window"):
        validate_sales_distributed_tables(applier.generator, malformed)


def test_rejects_primary_key_drift_from_source(
    validated_tables: tuple[SalesDistributionApplier, dict, dict],
) -> None:
    applier, base, distributed = validated_tables
    malformed = _copy_tables(distributed)
    malformed["products"].at[0, "product_id"] = 999999
    malformed["quotations"].loc[
        malformed["quotations"]["product_id"] == 1,
        "product_id",
    ] = 999999

    with pytest.raises(ValueError, match="products.product_id changed"):
        validate_sales_distributed_tables(
            applier.generator,
            malformed,
            source_tables=base,
        )


def test_rejects_unapproved_field_drift_from_source(
    validated_tables: tuple[SalesDistributionApplier, dict, dict],
) -> None:
    applier, base, distributed = validated_tables
    malformed = _copy_tables(distributed)
    malformed["leads"].at[0, "lead_name"] = "Unexpected Replacement"

    with pytest.raises(ValueError, match="leads.lead_name changed unexpectedly"):
        validate_sales_distributed_tables(
            applier.generator,
            malformed,
            source_tables=base,
        )


def test_validator_rejects_non_sales_settings() -> None:
    settings = GenerationSettings.from_config_files("crm", "dev")
    with pytest.raises(ValueError, match="only supports the sales domain"):
        SalesDistributedTablesValidator(DeterministicGenerator(settings))


def _copy_tables(tables: dict) -> dict:
    return {name: table.copy(deep=True) for name, table in tables.items()}
