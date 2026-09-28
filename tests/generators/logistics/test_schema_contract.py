from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import duckdb

from generators.core.schema_contract import ddl_constraints
from generators.core.schema_contract import ddl_table_bodies
from generators.core.schema_contract import ddl_table_specs
from generators.core.schema_contract import header_table_specs


ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DIR = ROOT / "schemas" / "logistics"
DDL_PATH = SCHEMA_DIR / "logistics_ddl.sql"
DBML_PATH = SCHEMA_DIR / "logistics_er.dbml"
HEADER_PATH = SCHEMA_DIR / "logistics_csv_header_spec.md"


def _field(
    sql_type: str,
    nullable: bool,
    default: str | None = None,
) -> dict[str, Any]:
    return {"type": sql_type, "nullable": nullable, "default": default}


EXPECTED_TABLE_SPECS: dict[str, dict[str, dict[str, Any]]] = {
    "carriers": {
        "carrier_id": _field("INTEGER", False),
        "carrier_name": _field("VARCHAR(255)", False),
        "service_level": _field("VARCHAR(32)", False),
        "carrier_type": _field("VARCHAR(32)", True),
        "base_rate": _field("DECIMAL(10,2)", True),
        "currency_code": _field("CHAR(3)", False, "USD"),
        "is_active": _field("BOOLEAN", False, "TRUE"),
        "created_at": _field("TIMESTAMP", False),
    },
    "warehouses": {
        "warehouse_id": _field("INTEGER", False),
        "warehouse_name": _field("VARCHAR(255)", False),
        "region": _field("VARCHAR(32)", True),
        "country_code": _field("CHAR(2)", True),
        "capacity_units": _field("INTEGER", True),
        "utilization_pct": _field("DECIMAL(5,2)", True),
        "is_active": _field("BOOLEAN", False, "TRUE"),
        "created_at": _field("TIMESTAMP", False),
    },
    "orders": {
        "order_id": _field("INTEGER", False),
        "warehouse_id": _field("INTEGER", True),
        "customer_name": _field("VARCHAR(255)", False),
        "order_date": _field("DATE", False),
        "status": _field("VARCHAR(32)", False),
        "order_priority": _field("VARCHAR(32)", False),
        "total_amount": _field("DECIMAL(15,2)", False),
        "currency_code": _field("CHAR(3)", False, "USD"),
        "created_at": _field("TIMESTAMP", False),
    },
    "shipments": {
        "shipment_id": _field("INTEGER", False),
        "order_id": _field("INTEGER", False),
        "carrier_id": _field("INTEGER", False),
        "tracking_number": _field("VARCHAR(128)", False),
        "ship_date": _field("DATE", True),
        "delivery_date": _field("DATE", True),
        "status": _field("VARCHAR(32)", False),
        "shipping_cost": _field("DECIMAL(15,2)", True),
        "currency_code": _field("CHAR(3)", False, "USD"),
        "created_at": _field("TIMESTAMP", False),
    },
    "inventory": {
        "inventory_id": _field("INTEGER", False),
        "warehouse_id": _field("INTEGER", False),
        "product_sku": _field("VARCHAR(64)", False),
        "product_category": _field("VARCHAR(64)", True),
        "quantity_on_hand": _field("INTEGER", False),
        "reorder_point": _field("INTEGER", True),
        "last_updated_at": _field("TIMESTAMP", False),
        "created_at": _field("TIMESTAMP", False),
    },
}

EXPECTED_PRIMARY_KEYS = {
    "carriers": ("carrier_id",),
    "warehouses": ("warehouse_id",),
    "orders": ("order_id",),
    "shipments": ("shipment_id",),
    "inventory": ("inventory_id",),
}

EXPECTED_FOREIGN_KEYS = {
    ("shipments", "order_id", "orders", "order_id"),
    ("shipments", "carrier_id", "carriers", "carrier_id"),
    ("inventory", "warehouse_id", "warehouses", "warehouse_id"),
}


def _dbml_table_fields(path: Path) -> dict[str, list[str]]:
    """Extract ordered DBML table and field names for contract comparison."""

    text = path.read_text(encoding="utf-8")
    tables: dict[str, list[str]] = {}
    for table_name, body in re.findall(
        r"^Table\s+([a-z_]+)\s*\{(.*?)^\}",
        text,
        re.MULTILINE | re.DOTALL,
    ):
        tables[table_name] = re.findall(
            r"^\s{2}([a-z_]+)\s+"
            r"(?:integer|varchar|char\(\d+\)|decimal\(\d+,\s*\d+\)|"
            r"boolean|date|timestamp)(?=\s|$)",
            body,
            re.MULTILINE | re.IGNORECASE,
        )
    return tables


def test_ddl_freezes_exact_logistics_table_and_field_contract() -> None:
    assert ddl_table_specs(DDL_PATH) == EXPECTED_TABLE_SPECS


def test_csv_headers_match_ddl_order_types_and_nullability() -> None:
    headers = header_table_specs(HEADER_PATH)

    assert list(headers) == list(EXPECTED_TABLE_SPECS)
    for table_name, fields in headers.items():
        expected = EXPECTED_TABLE_SPECS[table_name]
        assert [field["name"] for field in fields] == list(expected)
        assert [field["type"] for field in fields] == [
            specification["type"] for specification in expected.values()
        ]
        assert [field["nullable"] for field in fields] == [
            specification["nullable"] for specification in expected.values()
        ]


def test_dbml_matches_ddl_table_and_field_order() -> None:
    assert _dbml_table_fields(DBML_PATH) == {
        table_name: list(fields)
        for table_name, fields in EXPECTED_TABLE_SPECS.items()
    }


def test_ddl_freezes_primary_and_physical_foreign_keys() -> None:
    constraints = ddl_constraints(DDL_PATH)

    assert constraints["primary_keys"] == EXPECTED_PRIMARY_KEYS
    assert constraints["foreign_keys"] == EXPECTED_FOREIGN_KEYS
    assert (
        "orders",
        "warehouse_id",
        "warehouses",
        "warehouse_id",
    ) not in constraints["foreign_keys"]


def test_erd_preserves_visual_only_order_warehouse_relationship() -> None:
    dbml = DBML_PATH.read_text(encoding="utf-8")
    ddl = DDL_PATH.read_text(encoding="utf-8")
    orders_body = dict(ddl_table_bodies(DDL_PATH))["orders"]

    assert "Ref: orders.warehouse_id > warehouses.warehouse_id" in dbml
    assert "orders.warehouse_id deliberately has no SQL FK" in ddl
    assert not re.search(
        r"FOREIGN\s+KEY\s*\(warehouse_id\)\s*"
        r"REFERENCES\s+warehouses\s*\(warehouse_id\)",
        orders_body,
        re.IGNORECASE,
    )


def test_canonical_logistics_ddl_executes_in_duckdb() -> None:
    with duckdb.connect(database=":memory:") as connection:
        connection.execute(DDL_PATH.read_text(encoding="utf-8"))
        actual_tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'main'
                """
            ).fetchall()
        }

    assert actual_tables == set(EXPECTED_TABLE_SPECS)
