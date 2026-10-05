from __future__ import annotations

from pandas.testing import assert_frame_equal

from generators.logistics.generator import LogisticsBaseEntityGenerator
from generators.logistics.generator import LOGISTICS_COLUMN_CONTRACTS


def _generate() -> dict[str, object]:
    return LogisticsBaseEntityGenerator.for_profile("dev").generate_tables()


def test_base_generator_is_deterministic_and_uses_configured_contracts() -> None:
    first = _generate()
    second = _generate()
    expected_rows = {
        "carriers": 10,
        "warehouses": 10,
        "orders": 1000,
        "shipments": 1500,
        "inventory": 500,
    }

    assert list(first) == list(LOGISTICS_COLUMN_CONTRACTS)
    for table_name, expected_count in expected_rows.items():
        assert len(first[table_name]) == expected_count
        assert (
            first[table_name].columns.tolist() == LOGISTICS_COLUMN_CONTRACTS[table_name]
        )
        assert_frame_equal(first[table_name], second[table_name])


def test_base_relationships_and_business_keys_are_clean() -> None:
    tables = _generate()
    warehouse_ids = set(tables["warehouses"]["warehouse_id"])
    order_ids = set(tables["orders"]["order_id"])
    carrier_ids = set(tables["carriers"]["carrier_id"])

    assert set(tables["orders"]["warehouse_id"]) <= warehouse_ids
    assert set(tables["shipments"]["order_id"]) <= order_ids
    assert set(tables["shipments"]["carrier_id"]) <= carrier_ids
    assert set(tables["inventory"]["warehouse_id"]) <= warehouse_ids
    assert not tables["shipments"]["tracking_number"].duplicated().any()
    assert not tables["inventory"].duplicated(["warehouse_id", "product_sku"]).any()


def test_base_shipments_preserve_status_dates_and_required_paths() -> None:
    tables = _generate()
    orders = tables["orders"]
    shipments = tables["shipments"]
    merged = shipments.merge(
        orders[["order_id", "order_date", "status"]],
        on="order_id",
        suffixes=("_shipment", "_order"),
    )

    shipped = merged[merged["ship_date"] != ""]
    delivered = merged[merged["delivery_date"] != ""]
    assert (shipped["ship_date"] >= shipped["order_date"]).all()
    assert (delivered["delivery_date"] >= delivered["ship_date"]).all()
    assert (merged.loc[merged["status_shipment"] == "Booked", "ship_date"] == "").all()
    assert (
        merged.loc[merged["status_shipment"] == "Delivered", "delivery_date"] != ""
    ).all()
    delivered_order_ids = set(orders.loc[orders["status"] == "Delivered", "order_id"])
    delivered_evidence = set(
        shipments.loc[shipments["status"] == "Delivered", "order_id"]
    )
    assert delivered_order_ids <= delivered_evidence
    assert len(order_ids_without_shipments(orders, shipments)) >= 1

    carriers_per_order = shipments.groupby("order_id")["carrier_id"].nunique()
    orders_per_carrier = shipments.groupby("carrier_id")["order_id"].nunique()
    assert int(carriers_per_order.max()) > 1
    assert int(orders_per_carrier.max()) > 1


def test_base_values_follow_currency_inventory_and_domain_contracts() -> None:
    tables = _generate()

    for table_name in ("carriers", "orders", "shipments"):
        assert set(tables[table_name]["currency_code"]) == {"USD"}
    assert set(tables["orders"]["status"]) == {
        "Pending",
        "Shipped",
        "Delivered",
        "Cancelled",
        "Returned",
    }
    assert set(tables["shipments"]["status"]) == {
        "Booked",
        "InTransit",
        "Delivered",
        "Failed",
        "Lost",
    }
    inventory = tables["inventory"]
    assert (inventory["quantity_on_hand"].astype(int) >= 0).all()
    populated_reorder = inventory.loc[inventory["reorder_point"] != "", "reorder_point"]
    assert (populated_reorder.astype(int) >= 0).all()
    assert (inventory["created_at"] <= inventory["last_updated_at"]).all()
    assert int(inventory.loc[0, "quantity_on_hand"]) < int(
        inventory.loc[0, "reorder_point"]
    )
    assert int(inventory.loc[1, "quantity_on_hand"]) > int(
        inventory.loc[1, "reorder_point"]
    )


def order_ids_without_shipments(orders: object, shipments: object) -> set[int]:
    return set(orders["order_id"]) - set(shipments["order_id"])
