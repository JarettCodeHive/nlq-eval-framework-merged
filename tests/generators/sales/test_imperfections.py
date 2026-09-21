from __future__ import annotations

from decimal import Decimal
import importlib.util

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.core.imperfections import count_from_pct
from generators.sales.distributions import SalesDistributionApplier
from generators.sales.generator import SALES_COLUMN_CONTRACTS
from generators.sales.imperfections import SalesImperfectionInjector


pytestmark = pytest.mark.skipif(
    not all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    ),
    reason="numpy, pandas, and Faker are not installed",
)


@pytest.fixture(scope="module")
def distributed_and_imperfect() -> tuple[SalesImperfectionInjector, dict, dict]:
    injector = SalesImperfectionInjector.for_profile("dev")
    distributed = SalesDistributionApplier(
        injector.generator
    ).generate_distributed_tables()
    snapshot = {name: table.copy(deep=True) for name, table in distributed.items()}
    imperfect = injector.apply_to_tables(distributed)
    assert all(distributed[name].equals(snapshot[name]) for name in distributed)
    return injector, distributed, imperfect


def test_imperfections_preserve_schema_and_expected_row_counts(
    distributed_and_imperfect: tuple[SalesImperfectionInjector, dict, dict],
) -> None:
    injector, distributed, imperfect = distributed_and_imperfect
    expected_duplicates = count_from_pct(
        len(distributed["quotations"]),
        float(injector.config["duplicate_pct"]),
    )

    for table_name, columns in SALES_COLUMN_CONTRACTS.items():
        assert imperfect[table_name].columns.tolist() == columns
        expected_rows = len(distributed[table_name])
        if table_name == "quotations":
            expected_rows += expected_duplicates
        assert len(imperfect[table_name]) == expected_rows
    assert imperfect["quotations"]["quotation_id"].is_unique


def test_missing_product_prices_match_configured_rate_and_preserve_quote_prices(
    distributed_and_imperfect: tuple[SalesImperfectionInjector, dict, dict],
) -> None:
    injector, distributed, imperfect = distributed_and_imperfect
    expected = count_from_pct(
        len(distributed["products"]), float(injector.config["null_pct"])
    )

    assert int(imperfect["products"]["list_price"].astype(str).eq("").sum()) == expected
    assert (
        imperfect["quotations"]
        .iloc[: len(distributed["quotations"])]["unit_price"]
        .equals(distributed["quotations"]["unit_price"])
    )


def test_near_duplicate_quote_lines_keep_business_keys_and_vary_values(
    distributed_and_imperfect: tuple[SalesImperfectionInjector, dict, dict],
) -> None:
    injector, distributed, imperfect = distributed_and_imperfect
    duplicate_count = count_from_pct(
        len(distributed["quotations"]), float(injector.config["duplicate_pct"])
    )
    duplicates = imperfect["quotations"].tail(duplicate_count)
    base_by_key = distributed["quotations"].set_index(
        ["deal_id", "product_id", "quote_number"]
    )

    assert (
        duplicates["quotation_id"].min()
        > distributed["quotations"]["quotation_id"].max()
    )
    for row in duplicates.itertuples(index=False):
        source = base_by_key.loc[(row.deal_id, row.product_id, row.quote_number)]
        assert row.quantity != source["quantity"]
        assert row.discount_pct != source["discount_pct"]
        assert row.created_at <= row.quoted_at


def test_deal_amount_outliers_match_configured_count_range_and_scale(
    distributed_and_imperfect: tuple[SalesImperfectionInjector, dict, dict],
) -> None:
    injector, distributed, imperfect = distributed_and_imperfect
    target = injector.sales_config["imperfection_targets"]["deal_amount_outliers"]
    expected = count_from_pct(
        len(distributed["deals"]), float(injector.config["outlier_pct"])
    )
    amounts = imperfect["deals"]["deal_amount"].map(Decimal)
    outliers = amounts[amounts >= Decimal(target["minimum_value"])]

    assert len(outliers) == expected
    assert outliers.between(
        Decimal(target["minimum_value"]), Decimal(target["maximum_value"])
    ).all()
    assert all(value.as_tuple().exponent == -2 for value in outliers)


def test_product_boundary_timestamps_are_present_at_fixed_positions(
    distributed_and_imperfect: tuple[SalesImperfectionInjector, dict, dict],
) -> None:
    injector, _, imperfect = distributed_and_imperfect
    expected = [f"{value}T00:00:00" for value in injector.config["boundary_dates"]]

    assert (
        imperfect["products"]["created_at"].iloc[: len(expected)].tolist() == expected
    )


def test_physical_relationships_remain_valid(
    distributed_and_imperfect: tuple[SalesImperfectionInjector, dict, dict],
) -> None:
    _, _, imperfect = distributed_and_imperfect

    assert set(imperfect["deals"]["lead_id"]).issubset(
        set(imperfect["leads"]["lead_id"])
    )
    assert set(imperfect["quotations"]["deal_id"]).issubset(
        set(imperfect["deals"]["deal_id"])
    )
    assert set(imperfect["quotations"]["product_id"]).issubset(
        set(imperfect["products"]["product_id"])
    )


def test_imperfect_generation_is_reproducible() -> None:
    first = SalesImperfectionInjector.for_profile("dev").generate_imperfect_tables()
    second = SalesImperfectionInjector.for_profile("dev").generate_imperfect_tables()

    for table_name in SALES_COLUMN_CONTRACTS:
        assert first[table_name].equals(second[table_name])


def test_imperfection_preflight_rejects_invalid_distributed_input(
    distributed_and_imperfect: tuple[SalesImperfectionInjector, dict, dict],
) -> None:
    injector, distributed, _ = distributed_and_imperfect
    malformed = {name: table.copy(deep=True) for name, table in distributed.items()}
    malformed["targets"].at[0, "quota_amount"] = "9999999.00"

    with pytest.raises(ValueError, match="outside configured distribution bounds"):
        injector.apply_to_tables(malformed)


def test_injector_rejects_non_sales_settings() -> None:
    settings = GenerationSettings.from_config_files("crm", "dev")
    with pytest.raises(ValueError, match="only supports sales"):
        SalesImperfectionInjector(DeterministicGenerator(settings))
