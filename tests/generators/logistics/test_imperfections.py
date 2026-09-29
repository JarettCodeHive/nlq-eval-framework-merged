from __future__ import annotations

from datetime import date
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

from generators.core.imperfections import count_from_pct
from generators.logistics.distributions import LogisticsDistributionApplier
from generators.logistics.imperfections import LogisticsImperfectionInjector
from generators.logistics.validators.imperfect_tables import (
    validate_logistics_imperfect_tables,
)


@pytest.fixture(scope="module")
def stages() -> tuple[
    LogisticsImperfectionInjector,
    dict[str, Any],
    dict[str, Any],
]:
    injector = LogisticsImperfectionInjector.for_profile("dev")
    distributed = LogisticsDistributionApplier(
        injector.generator
    ).generate_distributed_tables()
    snapshot = {
        table_name: table.copy(deep=True) for table_name, table in distributed.items()
    }
    imperfect = injector.apply_to_tables(distributed)
    assert all(distributed[name].equals(snapshot[name]) for name in distributed)
    return injector, distributed, imperfect


def test_near_duplicate_shipments_use_fresh_ids_and_vary_cost(
    stages: tuple[LogisticsImperfectionInjector, dict[str, Any], dict[str, Any]],
) -> None:
    injector, distributed, imperfect = stages
    base_count = len(distributed["shipments"])
    expected = count_from_pct(base_count, float(injector.config["duplicate_pct"]))
    duplicates = imperfect["shipments"].iloc[base_count:]
    source = distributed["shipments"].set_index("tracking_number")

    assert len(duplicates) == expected
    assert duplicates["shipment_id"].tolist() == list(
        range(base_count + 1, base_count + expected + 1)
    )
    for row in duplicates.itertuples(index=False):
        original = source.loc[row.tracking_number]
        assert row.order_id == original["order_id"]
        assert row.carrier_id == original["carrier_id"]
        assert row.shipping_cost != original["shipping_cost"]


def test_missing_and_orphan_warehouses_are_exact_and_disjoint(
    stages: tuple[LogisticsImperfectionInjector, dict[str, Any], dict[str, Any]],
) -> None:
    injector, distributed, imperfect = stages
    orders = imperfect["orders"]
    warehouse_ids = set(imperfect["warehouses"]["warehouse_id"])
    null_rows = orders[orders["warehouse_id"] == ""]
    orphan_rows = orders[
        orders["warehouse_id"].map(
            lambda value: value != "" and value not in warehouse_ids
        )
    ]

    assert len(null_rows) == count_from_pct(
        len(distributed["orders"]), float(injector.config["null_pct"])
    )
    assert len(orphan_rows) == count_from_pct(
        len(distributed["orders"]),
        float(injector.targets["orphaned_order_warehouses"]["rate_pct"]),
    )
    namespace = int(
        injector.targets["orphaned_order_warehouses"]["namespace_base"]
    )
    assert all(
        int(row.warehouse_id) == namespace + int(row.order_id)
        for row in orphan_rows.itertuples(index=False)
    )
    assert set(null_rows["order_id"]).isdisjoint(set(orphan_rows["order_id"]))


def test_outliers_and_boundary_paths_match_the_declared_contract(
    stages: tuple[LogisticsImperfectionInjector, dict[str, Any], dict[str, Any]],
) -> None:
    injector, distributed, imperfect = stages
    target = injector.targets["order_total_outliers"]
    minimum = Decimal(str(target["minimum_value"]))
    maximum = Decimal(str(target["maximum_value"]))
    amounts = imperfect["orders"]["total_amount"].map(Decimal)
    outliers = amounts[amounts >= minimum]
    boundaries = set(injector.config["boundary_dates"])

    assert len(outliers) == count_from_pct(
        len(distributed["orders"]), float(injector.config["outlier_pct"])
    )
    assert outliers.le(maximum).all()
    assert outliers.map(lambda value: value.as_tuple().exponent == -2).all()
    assert boundaries <= set(imperfect["orders"]["order_date"])
    assert boundaries <= set(imperfect["shipments"]["ship_date"])
    assert boundaries <= set(imperfect["shipments"]["delivery_date"])


def test_imperfect_shipments_preserve_physical_links_and_chronology(
    stages: tuple[LogisticsImperfectionInjector, dict[str, Any], dict[str, Any]],
) -> None:
    _, _, tables = stages
    orders = tables["orders"].set_index("order_id")
    shipments = tables["shipments"]

    assert set(shipments["order_id"]) <= set(orders.index)
    assert set(shipments["carrier_id"]) <= set(tables["carriers"]["carrier_id"])
    assert set(tables["inventory"]["warehouse_id"]) <= set(
        tables["warehouses"]["warehouse_id"]
    )
    assert set(shipments["currency_code"]) == {"USD"}
    for row in shipments.itertuples(index=False):
        order = orders.loc[row.order_id]
        order_date = date.fromisoformat(order["order_date"])
        created = datetime.fromisoformat(row.created_at)
        assert created >= datetime.fromisoformat(order["created_at"])
        if row.ship_date:
            ship_date = date.fromisoformat(row.ship_date)
            assert ship_date >= order_date
            assert created.date() <= ship_date
        if row.delivery_date:
            assert date.fromisoformat(row.delivery_date) >= date.fromisoformat(
                row.ship_date
            )


def test_imperfect_validator_rejects_byte_identical_duplicate(
    stages: tuple[LogisticsImperfectionInjector, dict[str, Any], dict[str, Any]],
) -> None:
    injector, distributed, imperfect = stages
    changed = {name: table.copy(deep=True) for name, table in imperfect.items()}
    duplicate_position = len(distributed["shipments"])
    tracking_number = changed["shipments"].at[
        duplicate_position, "tracking_number"
    ]
    source = distributed["shipments"].loc[
        distributed["shipments"]["tracking_number"] == tracking_number
    ].iloc[0]
    changed["shipments"].at[duplicate_position, "shipping_cost"] = source[
        "shipping_cost"
    ]

    with pytest.raises(ValueError, match="byte-identical"):
        validate_logistics_imperfect_tables(
            injector.generator,
            changed,
            source_tables=distributed,
        )


def test_imperfect_validator_rejects_undeclared_orphan_namespace(
    stages: tuple[LogisticsImperfectionInjector, dict[str, Any], dict[str, Any]],
) -> None:
    injector, distributed, imperfect = stages
    changed = {name: table.copy(deep=True) for name, table in imperfect.items()}
    warehouse_ids = set(changed["warehouses"]["warehouse_id"])
    position = next(
        position
        for position, value in enumerate(changed["orders"]["warehouse_id"])
        if value != "" and value not in warehouse_ids
    )
    changed["orders"].at[position, "warehouse_id"] = 800000000

    with pytest.raises(ValueError, match="undeclared warehouse orphan"):
        validate_logistics_imperfect_tables(
            injector.generator,
            changed,
            source_tables=distributed,
        )


def test_imperfect_validator_rejects_collateral_field_change(
    stages: tuple[LogisticsImperfectionInjector, dict[str, Any], dict[str, Any]],
) -> None:
    injector, distributed, imperfect = stages
    changed = {name: table.copy(deep=True) for name, table in imperfect.items()}
    changed["carriers"].at[0, "carrier_name"] = "Unexpected Carrier"

    with pytest.raises(ValueError, match="carrier_name changed unexpectedly"):
        validate_logistics_imperfect_tables(
            injector.generator,
            changed,
            source_tables=distributed,
        )
