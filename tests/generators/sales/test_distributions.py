from __future__ import annotations

from datetime import date
from datetime import datetime
from decimal import Decimal
import importlib.util
import inspect

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.sales.distributions import SalesDistributionApplier
from generators.sales.generator import SALES_COLUMN_CONTRACTS
from generators.sales.generator import SalesBaseEntityGenerator
from generators.sales.validators.generated_tables import (
    validate_sales_generated_tables,
)


pytestmark = pytest.mark.skipif(
    not all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    ),
    reason="numpy, pandas, and Faker are not installed",
)


@pytest.fixture(scope="module")
def base_and_distributed() -> tuple[dict, dict, SalesDistributionApplier]:
    base = SalesBaseEntityGenerator.for_profile("dev").generate_tables()
    snapshot = {name: table.copy(deep=True) for name, table in base.items()}
    applier = SalesDistributionApplier.for_profile("dev")
    distributed = applier.apply_to_tables(base)
    assert all(base[name].equals(snapshot[name]) for name in base)
    return base, distributed, applier


def test_sales_distributions_preserve_contract_and_relational_guards(
    base_and_distributed: tuple[dict, dict, SalesDistributionApplier],
) -> None:
    base, distributed, applier = base_and_distributed

    assert list(distributed) == list(SALES_COLUMN_CONTRACTS)
    for table_name, columns in SALES_COLUMN_CONTRACTS.items():
        assert distributed[table_name].columns.tolist() == columns
        assert len(distributed[table_name]) == len(base[table_name])
    validate_sales_generated_tables(applier.generator, distributed)


@pytest.mark.parametrize(
    ("table_name", "column_name", "settings_key"),
    [
        ("deals", "deal_amount", "deal_amount"),
        ("targets", "quota_amount", "quota_amount"),
    ],
)
def test_pareto_amounts_are_bounded_and_fixed_scale(
    base_and_distributed: tuple[dict, dict, SalesDistributionApplier],
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
    assert values.map(lambda value: Decimal(value).as_tuple().exponent == -2).all()


def test_poisson_frequency_keeps_exact_unique_bridge_coverage(
    base_and_distributed: tuple[dict, dict, SalesDistributionApplier],
) -> None:
    base, distributed, _ = base_and_distributed
    quotations = distributed["quotations"]
    frequencies = quotations.groupby("deal_id").size()

    assert len(quotations) == len(base["quotations"])
    assert frequencies.sum() == len(quotations)
    assert frequencies.nunique() > 1
    assert set(quotations["deal_id"]) == set(distributed["deals"]["deal_id"])
    assert set(quotations["product_id"]) == set(distributed["products"]["product_id"])
    assert not quotations.duplicated(["deal_id", "product_id"]).any()


def test_clustered_dates_change_and_preserve_chronology(
    base_and_distributed: tuple[dict, dict, SalesDistributionApplier],
) -> None:
    base, distributed, _ = base_and_distributed
    assert not base["leads"]["created_at"].equals(distributed["leads"]["created_at"])
    assert not base["products"]["created_at"].equals(
        distributed["products"]["created_at"]
    )

    leads = distributed["leads"].set_index("lead_id")
    for row in distributed["deals"].itertuples(index=False):
        created = datetime.fromisoformat(row.created_at)
        assert created >= datetime.fromisoformat(leads.loc[row.lead_id, "created_at"])
        assert date.fromisoformat(row.expected_close_date) >= created.date()
        if row.close_date:
            assert date.fromisoformat(row.close_date) >= created.date()

    deals = distributed["deals"].set_index("deal_id")
    for row in distributed["quotations"].itertuples(index=False):
        created = datetime.fromisoformat(row.created_at)
        quoted = datetime.fromisoformat(row.quoted_at)
        assert created >= datetime.fromisoformat(deals.loc[row.deal_id, "created_at"])
        assert created <= quoted


def test_distributed_generation_is_reproducible() -> None:
    first = SalesDistributionApplier.for_profile("dev").generate_distributed_tables()
    second = SalesDistributionApplier.for_profile("dev").generate_distributed_tables()

    for table_name in SALES_COLUMN_CONTRACTS:
        assert first[table_name].equals(second[table_name])


def test_distribution_applier_rejects_non_sales_settings() -> None:
    settings = GenerationSettings.from_config_files("crm", "dev")
    with pytest.raises(ValueError, match="only supports sales"):
        SalesDistributionApplier(DeterministicGenerator(settings))


def test_distribution_stage_does_not_read_written_datasets() -> None:
    source = inspect.getsource(
        __import__("generators.sales.distributions", fromlist=["*"])
    )
    assert "read_csv(" not in source
    assert "read_sql(" not in source
