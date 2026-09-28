from __future__ import annotations

from typing import Any

import pytest
from pandas.testing import assert_frame_equal

from generators.logistics.distributions import LogisticsDistributionApplier
from generators.logistics.generator import LogisticsBaseEntityGenerator
from generators.logistics.validators.distributed_tables import (
    validate_logistics_distributed_tables,
)


@pytest.fixture(scope="module")
def stages() -> tuple[dict[str, Any], dict[str, Any]]:
    applier = LogisticsDistributionApplier.for_profile("dev")
    base = LogisticsBaseEntityGenerator(applier.generator).generate_tables()
    snapshots = {name: table.copy(deep=True) for name, table in base.items()}
    distributed = applier.apply_to_tables(base)
    for table_name in base:
        assert_frame_equal(base[table_name], snapshots[table_name])
    return base, distributed


def test_distribution_application_is_deterministic() -> None:
    first = LogisticsDistributionApplier.for_profile(
        "dev"
    ).generate_distributed_tables()
    second = LogisticsDistributionApplier.for_profile(
        "dev"
    ).generate_distributed_tables()

    for table_name in first:
        assert_frame_equal(first[table_name], second[table_name])


def test_pareto_order_totals_are_bounded_fixed_scale(
    stages: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    base, distributed = stages
    amounts = distributed["orders"]["total_amount"]

    assert not amounts.equals(base["orders"]["total_amount"])
    assert amounts.str.fullmatch(r"\d+\.\d{2}").all()
    numeric = amounts.astype(float)
    assert numeric.between(25, 500000).all()
    assert numeric.quantile(0.9) > numeric.median()


def test_poisson_frequency_preserves_exact_rows_and_unshipped_orders(
    stages: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    base, distributed = stages
    base_shipments = base["shipments"]
    shipments = distributed["shipments"]

    assert len(shipments) == len(base_shipments) == 1500
    assert set(shipments["order_id"]) == set(base_shipments["order_id"])
    counts = shipments.groupby("order_id").size()
    assert int(counts.min()) >= 1
    assert int(counts.max()) > int(counts.min())
    assert shipments.groupby("order_id")["carrier_id"].nunique().max() > 1
    assert shipments.groupby("carrier_id")["order_id"].nunique().max() > 1
    assert not shipments["tracking_number"].duplicated().any()


def test_gaussian_dates_change_values_and_preserve_chronology(
    stages: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    base, distributed = stages
    orders = distributed["orders"]
    shipments = distributed["shipments"]
    merged = shipments.merge(orders[["order_id", "order_date"]], on="order_id")

    assert not orders["order_date"].equals(base["orders"]["order_date"])
    assert not shipments["ship_date"].equals(base["shipments"]["ship_date"])
    shipped = merged[merged["ship_date"] != ""]
    delivered = merged[merged["delivery_date"] != ""]
    assert (shipped["ship_date"] >= shipped["order_date"]).all()
    assert (delivered["delivery_date"] >= delivered["ship_date"]).all()
    assert (
        distributed["inventory"]["created_at"]
        <= distributed["inventory"]["last_updated_at"]
    ).all()


def test_distributed_validator_rejects_undeclared_field_drift(
    stages: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    base, distributed = stages
    changed = {name: table.copy(deep=True) for name, table in distributed.items()}
    changed["carriers"].loc[0, "carrier_name"] = "Unexpected Carrier"

    with pytest.raises(ValueError, match="carrier_name changed unexpectedly"):
        validate_logistics_distributed_tables(
            LogisticsDistributionApplier.for_profile("dev").generator,
            changed,
            source_tables=base,
        )


def test_distributed_validator_rejects_missing_pareto_tail(
    stages: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    base, distributed = stages
    changed = {name: table.copy(deep=True) for name, table in distributed.items()}
    changed["orders"]["total_amount"] = "25.00"

    with pytest.raises(ValueError, match="observable Pareto tail"):
        validate_logistics_distributed_tables(
            LogisticsDistributionApplier.for_profile("dev").generator,
            changed,
            source_tables=base,
        )


def test_distributed_validator_rejects_inventory_date_outside_window(
    stages: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    base, distributed = stages
    changed = {name: table.copy(deep=True) for name, table in distributed.items()}
    changed["inventory"].loc[0, "last_updated_at"] = "2099-01-01T12:00:00"

    with pytest.raises(ValueError, match="outside its distribution window"):
        validate_logistics_distributed_tables(
            LogisticsDistributionApplier.for_profile("dev").generator,
            changed,
            source_tables=base,
        )
