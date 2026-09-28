from __future__ import annotations

from copy import deepcopy

import pytest

from generators.logistics.config import load_logistics_config
from generators.logistics.generator import build_logistics_column_contracts
from generators.logistics.generator import LOGISTICS_COLUMN_CONTRACTS


EXPECTED_COLUMNS = {
    "carriers": [
        "carrier_id",
        "carrier_name",
        "service_level",
        "carrier_type",
        "base_rate",
        "currency_code",
        "is_active",
        "created_at",
    ],
    "warehouses": [
        "warehouse_id",
        "warehouse_name",
        "region",
        "country_code",
        "capacity_units",
        "utilization_pct",
        "is_active",
        "created_at",
    ],
    "orders": [
        "order_id",
        "warehouse_id",
        "customer_name",
        "order_date",
        "status",
        "order_priority",
        "total_amount",
        "currency_code",
        "created_at",
    ],
    "shipments": [
        "shipment_id",
        "order_id",
        "carrier_id",
        "tracking_number",
        "ship_date",
        "delivery_date",
        "status",
        "shipping_cost",
        "currency_code",
        "created_at",
    ],
    "inventory": [
        "inventory_id",
        "warehouse_id",
        "product_sku",
        "product_category",
        "quantity_on_hand",
        "reorder_point",
        "last_updated_at",
        "created_at",
    ],
}


def test_logistics_column_contracts_are_config_derived() -> None:
    config = load_logistics_config()

    assert LOGISTICS_COLUMN_CONTRACTS == EXPECTED_COLUMNS
    assert build_logistics_column_contracts() == EXPECTED_COLUMNS
    assert {
        table_name: [field["name"] for field in config["tables"][table_name]["fields"]]
        for table_name in config["table_order"]
    } == LOGISTICS_COLUMN_CONTRACTS


def test_column_contract_builder_tracks_config_changes() -> None:
    config = deepcopy(load_logistics_config())
    config["tables"]["orders"]["fields"].append(
        {
            "name": "test_only_field",
            "type": "varchar",
            "max_length": 32,
            "nullable": True,
        }
    )

    contracts = build_logistics_column_contracts(config)

    assert contracts["orders"][-1] == "test_only_field"
    assert contracts["orders"][:-1] == EXPECTED_COLUMNS["orders"]


def test_column_contract_builder_rejects_duplicate_fields() -> None:
    config = deepcopy(load_logistics_config())
    config["tables"]["orders"]["fields"].append(
        deepcopy(config["tables"]["orders"]["fields"][0])
    )

    with pytest.raises(ValueError, match="column contract contains duplicates"):
        build_logistics_column_contracts(config)
