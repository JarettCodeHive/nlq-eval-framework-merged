from __future__ import annotations

from datetime import date
from datetime import datetime
from decimal import Decimal
import importlib.util
import inspect

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.sales.config import settings_for_profile
from generators.sales.generator import SALES_COLUMN_CONTRACTS
from generators.sales.generator import SalesBaseEntityGenerator


def _dependencies_available() -> bool:
    return all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    )


pytestmark = pytest.mark.skipif(
    not _dependencies_available(),
    reason="numpy, pandas, and Faker are not installed",
)


def test_generate_dev_base_tables_with_configured_shape() -> None:
    generator = SalesBaseEntityGenerator.for_profile("dev")
    tables = generator.generate_tables()

    assert list(tables) == list(SALES_COLUMN_CONTRACTS)
    for table_name, columns in SALES_COLUMN_CONTRACTS.items():
        assert tables[table_name].columns.tolist() == columns
        assert len(tables[table_name]) == generator.generator.row_count(table_name)

    assert tables["products"]["sku"].is_unique
    assert tables["products"]["list_price"].ne("").all()
    assert tables["deals"]["lead_id"].is_unique
    assert set(tables["deals"]["currency_code"]) == {"USD"}
    assert set(tables["products"]["currency_code"]) == {"USD"}
    assert set(tables["targets"]["currency_code"]) == {"USD"}


def test_base_relationships_and_business_ownership_are_valid() -> None:
    generator = SalesBaseEntityGenerator.for_profile("dev")
    tables = generator.generate_tables()
    leads = tables["leads"]
    deals = tables["deals"]
    products = tables["products"]
    quotations = tables["quotations"]
    targets = tables["targets"]

    assert set(deals["lead_id"]).issubset(set(leads["lead_id"]))
    assert set(quotations["deal_id"]).issubset(set(deals["deal_id"]))
    assert set(quotations["product_id"]).issubset(set(products["product_id"]))
    assert set(quotations["deal_id"]) == set(deals["deal_id"])
    assert set(quotations["product_id"]) == set(products["product_id"])
    assert not quotations.duplicated(["deal_id", "product_id"]).any()
    assert quotations.groupby("deal_id")["product_id"].nunique().max() >= 2
    assert quotations.groupby("product_id")["deal_id"].nunique().max() >= 2

    lead_lookup = leads.set_index("lead_id")
    converted = generator.sales_config["business_mappings"]["lead_conversion"][
        "deal_bearing_status"
    ]
    for row in deals.itertuples(index=False):
        lead = lead_lookup.loc[row.lead_id]
        assert lead["lead_status"] == converted
        assert lead["rep_name"] == row.rep_name
    assert set(leads["lead_id"]) - set(deals["lead_id"])

    representative_territories = (
        leads[["rep_name", "territory"]].drop_duplicates().groupby("rep_name").size()
    )
    assert representative_territories.max() == 1
    target_territories = dict(
        targets[["rep_name", "territory"]]
        .drop_duplicates()
        .itertuples(index=False, name=None)
    )
    for rep_name, territory in (
        leads[["rep_name", "territory"]]
        .drop_duplicates()
        .itertuples(index=False, name=None)
    ):
        assert target_territories[rep_name] == territory


def test_base_dates_and_target_periods_are_chronologically_valid() -> None:
    tables = SalesBaseEntityGenerator.for_profile("dev").generate_tables()
    leads = tables["leads"].set_index("lead_id")

    for row in tables["deals"].itertuples(index=False):
        created = datetime.fromisoformat(row.created_at)
        assert created >= datetime.fromisoformat(leads.loc[row.lead_id, "created_at"])
        assert date.fromisoformat(row.expected_close_date) >= created.date()
        if row.close_date:
            assert date.fromisoformat(row.close_date) >= created.date()

    deal_created = dict(
        zip(
            tables["deals"]["deal_id"],
            tables["deals"]["created_at"],
            strict=True,
        )
    )
    for row in tables["quotations"].itertuples(index=False):
        created = datetime.fromisoformat(row.created_at)
        quoted = datetime.fromisoformat(row.quoted_at)
        assert created >= datetime.fromisoformat(deal_created[row.deal_id])
        assert quoted >= created

    targets = tables["targets"].sort_values(["rep_name", "period_start"])
    for _, periods in targets.groupby("rep_name"):
        previous_end: date | None = None
        for row in periods.itertuples(index=False):
            start = date.fromisoformat(row.period_start)
            end = date.fromisoformat(row.period_end)
            assert start < end
            assert datetime.fromisoformat(row.created_at).date() < start
            if previous_end is not None:
                assert start >= previous_end
            previous_end = end


def test_base_covers_configured_enums_and_quota_join_cases() -> None:
    generator = SalesBaseEntityGenerator.for_profile("dev")
    tables = generator.generate_tables()
    values = generator.sales_config["domain_values"]

    assert set(tables["leads"]["lead_source"]) == set(values["lead_sources"])
    assert set(tables["deals"]["stage"]) == set(values["deal_stages"])
    assert set(tables["products"]["category"]) == set(values["product_categories"])
    assert set(tables["quotations"]["quote_status"]) == set(values["quote_statuses"])

    won_stage = generator.sales_config["business_mappings"]["quota_attainment"][
        "attained_stage"
    ]
    won = tables["deals"][tables["deals"]["stage"] == won_stage]
    joined = tables["targets"].merge(won, on="rep_name", how="left")
    matched = joined[
        (joined["close_date"] >= joined["period_start"])
        & (joined["close_date"] < joined["period_end"])
    ]
    assert not matched.empty

    attained_target_ids = set(matched["target_id"])
    assert set(tables["targets"]["target_id"]) - attained_target_ids


def test_base_numeric_values_follow_configured_ranges_and_scale() -> None:
    generator = SalesBaseEntityGenerator.for_profile("dev")
    tables = generator.generate_tables()
    product_rules = generator.generation_rules["products"]["list_price"]
    quote_rules = generator.generation_rules["quotations"]

    product_prices = tables["products"]["list_price"].map(Decimal)
    assert product_prices.between(
        Decimal(product_rules["minimum_amount"]),
        Decimal(product_rules["maximum_amount"]),
    ).all()
    assert (
        tables["quotations"]["quantity"]
        .between(
            quote_rules["quantity"]["minimum"],
            quote_rules["quantity"]["maximum"],
        )
        .all()
    )
    discounts = tables["quotations"]["discount_pct"].map(Decimal)
    assert discounts.between(
        Decimal(quote_rules["discount_pct"]["minimum"]),
        Decimal(quote_rules["discount_pct"]["maximum"]),
    ).all()
    assert all(
        len(value.rsplit(".", 1)[1]) == 2 for value in product_prices.astype(str)
    )
    assert all(Decimal(value) > 0 for value in tables["quotations"]["unit_price"])
    assert all(Decimal(value) > 0 for value in tables["targets"]["quota_amount"])


@pytest.mark.parametrize("profile", ["dev", "full"])
def test_base_generation_is_reproducible_for_both_profiles(profile: str) -> None:
    settings = settings_for_profile(profile)

    first = SalesBaseEntityGenerator(DeterministicGenerator(settings)).generate_tables()
    second = SalesBaseEntityGenerator(
        DeterministicGenerator(settings)
    ).generate_tables()

    pd = pytest.importorskip("pandas")
    for table_name in SALES_COLUMN_CONTRACTS:
        pd.testing.assert_frame_equal(first[table_name], second[table_name])


def test_unrelated_named_streams_do_not_change_sales_generation() -> None:
    """Protect table output from unrelated RNG and Faker stream consumption."""

    settings = settings_for_profile("dev")
    baseline = SalesBaseEntityGenerator(
        DeterministicGenerator(settings)
    ).generate_tables()

    isolated_generator = DeterministicGenerator(settings)
    isolated_generator.rng_for("sales:test:unrelated").random(100)
    unrelated_faker = isolated_generator.faker_for("sales:test:unrelated")
    for _ in range(100):
        unrelated_faker.name()
    generated = SalesBaseEntityGenerator(isolated_generator).generate_tables()

    pd = pytest.importorskip("pandas")
    for table_name in SALES_COLUMN_CONTRACTS:
        pd.testing.assert_frame_equal(baseline[table_name], generated[table_name])


def test_generator_rejects_non_sales_settings() -> None:
    with pytest.raises(ValueError, match="only supports the sales domain"):
        SalesBaseEntityGenerator(
            DeterministicGenerator(GenerationSettings.from_config_files("crm", "dev"))
        )


def test_generation_uses_no_wall_clock_or_external_dataset_inputs() -> None:
    source = inspect.getsource(__import__("generators.sales.generator", fromlist=["*"]))

    for forbidden_call in (
        "date.today(",
        "datetime.now(",
        "datetime.utcnow(",
        "pd.read_csv(",
        "pd.read_sql(",
    ):
        assert forbidden_call not in source
