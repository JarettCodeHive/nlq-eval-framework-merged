"""Immediate structural and relational guards for clean Logistics tables."""

from __future__ import annotations

import re
from datetime import date
from datetime import datetime
from datetime import time
from decimal import Decimal
from decimal import InvalidOperation
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.schema_contract import primary_key_fields
from generators.logistics.config import load_base_config
from generators.logistics.config import load_logistics_config


class LogisticsGeneratedTablesValidator:
    """Reject invalid clean Logistics tables before base generation returns."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "logistics":
            raise ValueError(
                "LogisticsGeneratedTablesValidator only supports logistics"
            )
        self.generator = generator
        self.settings = generator.settings
        self.base_config = load_base_config()
        self.config = load_logistics_config()

    def validate(self, tables: dict[str, Any]) -> None:
        """Validate clean shape, relationships, and Logistics invariants."""

        self._validate_table_contracts(tables)
        self._validate_keys_and_required_fields(tables)
        self._validate_foreign_keys(tables)
        self._validate_domain_values(tables)
        self._validate_decimal_fields(tables)
        self._validate_warehouses(tables)
        self._validate_orders_and_shipments(tables)
        self._validate_inventory(tables)
        self._validate_required_paths(tables)
        self._validate_currency(tables)
        self._validate_clean_stage(tables)

    def _validate_table_contracts(self, tables: dict[str, Any]) -> None:
        if tuple(tables) != self.settings.table_order:
            raise ValueError("Generated Logistics table order differs from config")
        for table_name in self.settings.table_order:
            expected_columns = [
                field["name"] for field in self.config["tables"][table_name]["fields"]
            ]
            if tables[table_name].columns.tolist() != expected_columns:
                raise ValueError(f"{table_name} generated columns differ from contract")
            expected_rows = self.generator.row_count(table_name)
            if len(tables[table_name]) != expected_rows:
                raise ValueError(
                    f"{table_name} generated row count differs from config: "
                    f"expected {expected_rows}, got {len(tables[table_name])}"
                )

    def _validate_keys_and_required_fields(self, tables: dict[str, Any]) -> None:
        for table_name in self.settings.table_order:
            table_config = self.config["tables"][table_name]
            table = tables[table_name]
            key_fields = primary_key_fields(table_config)
            if any(_has_blanks(table[field]) for field in key_fields):
                raise ValueError(f"{table_name} contains blank primary keys")
            if table.duplicated(key_fields).any():
                raise ValueError(f"{table_name} contains duplicate primary keys")
            for field in table_config["fields"]:
                if not field["nullable"] and _has_blanks(table[field["name"]]):
                    raise ValueError(
                        f"{table_name}.{field['name']} contains blank required values"
                    )

    def _validate_foreign_keys(self, tables: dict[str, Any]) -> None:
        for table_name, table_config in self.config["tables"].items():
            for field in table_config["fields"]:
                reference = field.get("references")
                if reference is None:
                    continue
                parent_values = set(
                    tables[reference["table"]][reference["field"]].tolist()
                )
                child_values = {
                    value
                    for value in tables[table_name][field["name"]].tolist()
                    if not _is_blank(value)
                }
                if child_values - parent_values:
                    raise ValueError(
                        f"{table_name}.{field['name']} contains orphan values"
                    )

    def _validate_domain_values(self, tables: dict[str, Any]) -> None:
        values = self.config["domain_values"]
        memberships = (
            ("carriers", "service_level", "service_levels"),
            ("carriers", "carrier_type", "carrier_types"),
            ("warehouses", "region", "regions"),
            ("orders", "status", "order_statuses"),
            ("orders", "order_priority", "order_priorities"),
            ("shipments", "status", "shipment_statuses"),
            ("inventory", "product_category", "product_categories"),
        )
        for table_name, field_name, value_name in memberships:
            actual = {
                value
                for value in tables[table_name][field_name].tolist()
                if not _is_blank(value)
            }
            if actual - set(values[value_name]):
                raise ValueError(
                    f"{table_name}.{field_name} contains unknown domain values"
                )

    def _validate_decimal_fields(self, tables: dict[str, Any]) -> None:
        for table_name, table_config in self.config["tables"].items():
            for field in table_config["fields"]:
                if field["type"] != "decimal":
                    continue
                for value in tables[table_name][field["name"]]:
                    if not _is_blank(value):
                        _validate_fixed_decimal(
                            value,
                            int(field["precision"]),
                            int(field["scale"]),
                            f"{table_name}.{field['name']}",
                        )

    def _validate_warehouses(self, tables: dict[str, Any]) -> None:
        mappings = self.config["business_mappings"]["region_country_codes"]
        for row in tables["warehouses"].itertuples(index=False):
            if row.region and row.country_code:
                if row.country_code not in mappings[row.region]:
                    raise ValueError("Warehouse region and country are inconsistent")
            if row.capacity_units != "" and int(row.capacity_units) < 0:
                raise ValueError("Warehouse capacity cannot be negative")
            if row.utilization_pct != "" and not (
                Decimal("0.00")
                <= Decimal(str(row.utilization_pct))
                <= Decimal("100.00")
            ):
                raise ValueError("Warehouse utilization is outside 0.00-100.00")

    def _validate_orders_and_shipments(self, tables: dict[str, Any]) -> None:
        orders = tables["orders"]
        shipments = tables["shipments"]
        warehouse_ids = set(tables["warehouses"]["warehouse_id"])
        if _has_blanks(orders["warehouse_id"]):
            raise ValueError("Base Logistics orders contain warehouse NULLs")
        if set(orders["warehouse_id"]) - warehouse_ids:
            raise ValueError("Base Logistics orders contain warehouse orphans")
        if shipments.duplicated(["order_id", "carrier_id", "tracking_number"]).any():
            raise ValueError("Base Logistics shipments contain duplicate business keys")
        if shipments["tracking_number"].duplicated().any():
            raise ValueError("Base Logistics tracking numbers are not unique")

        order_lookup = orders.set_index("order_id")
        for row in shipments.itertuples(index=False):
            order = order_lookup.loc[row.order_id]
            order_date = date.fromisoformat(order["order_date"])
            order_created = datetime.fromisoformat(order["created_at"])
            created = datetime.fromisoformat(row.created_at)
            if created < order_created:
                raise ValueError("Shipment creation precedes order creation")
            ship_date = date.fromisoformat(row.ship_date) if row.ship_date else None
            delivery_date = (
                date.fromisoformat(row.delivery_date) if row.delivery_date else None
            )
            if ship_date is not None and ship_date < order_date:
                raise ValueError("Shipment ship_date precedes order_date")
            if delivery_date is not None and (
                ship_date is None or delivery_date < ship_date
            ):
                raise ValueError("Shipment delivery chronology is invalid")
            if ship_date is not None and created > datetime.combine(
                ship_date, time.max
            ):
                raise ValueError("Shipment creation follows its first event date")
            _validate_shipment_status_dates(row.status, ship_date, delivery_date)

        evidence = self.config["business_mappings"]["order_status_evidence"]
        shipment_order_ids = set(shipments["order_id"])
        delivered_order_ids = set(
            shipments.loc[shipments["status"] == "Delivered", "order_id"]
        )
        for row in orders.itertuples(index=False):
            requirement = evidence[row.status]
            if requirement in {"shipment_required", "shipment_history_required"}:
                if row.order_id not in shipment_order_ids:
                    raise ValueError("Order lacks required shipment evidence")
            if requirement == "delivered_shipment_required":
                if row.order_id not in delivered_order_ids:
                    raise ValueError("Delivered order lacks delivered shipment")
            if Decimal(str(row.total_amount)) <= 0:
                raise ValueError("Order total must be positive")
            if datetime.fromisoformat(row.created_at).date() > date.fromisoformat(
                row.order_date
            ):
                raise ValueError("Order creation follows its order_date")

    def _validate_inventory(self, tables: dict[str, Any]) -> None:
        inventory = tables["inventory"]
        if inventory.duplicated(["warehouse_id", "product_sku"]).any():
            raise ValueError("Inventory contains duplicate warehouse/SKU pairs")
        below = False
        above = False
        for row in inventory.itertuples(index=False):
            quantity = int(row.quantity_on_hand)
            if quantity < 0:
                raise ValueError("Inventory quantity cannot be negative")
            if row.reorder_point != "":
                reorder = int(row.reorder_point)
                if reorder < 0:
                    raise ValueError("Inventory reorder point cannot be negative")
                below = below or quantity < reorder
                above = above or quantity > reorder
            if datetime.fromisoformat(row.created_at) > datetime.fromisoformat(
                row.last_updated_at
            ):
                raise ValueError("Inventory update precedes creation")
        if not below or not above:
            raise ValueError("Inventory lacks below/above reorder examples")

    def _validate_required_paths(self, tables: dict[str, Any]) -> None:
        orders = tables["orders"]
        shipments = tables["shipments"]
        if shipments.empty:
            raise ValueError("Logistics shipment join paths are empty")
        if not (set(orders["order_id"]) - set(shipments["order_id"])):
            raise ValueError("Logistics base data lacks an unshipped order")
        if shipments.groupby("order_id")["carrier_id"].nunique().max() < 2:
            raise ValueError("Logistics lacks an order using multiple carriers")
        if shipments.groupby("carrier_id")["order_id"].nunique().max() < 2:
            raise ValueError("Logistics lacks a carrier serving multiple orders")

    def _validate_currency(self, tables: dict[str, Any]) -> None:
        expected = self.config["business_mappings"]["currency_code"]
        for table_name in ("carriers", "orders", "shipments"):
            if set(tables[table_name]["currency_code"]) != {expected}:
                raise ValueError(f"{table_name} contains non-USD currency values")

    def _validate_clean_stage(self, tables: dict[str, Any]) -> None:
        outlier = self.config["imperfection_targets"]["order_total_outliers"]
        clean_maximum = Decimal(
            str(self.config["distributions"]["order_total"]["max_amount"])
        )
        if any(
            Decimal(str(value)) > clean_maximum
            for value in tables[outlier["table"]][outlier["field"]]
        ):
            raise ValueError("Base Logistics data already contains amount outliers")
        boundary_values = set(self.base_config["imperfections"]["boundary_dates"])
        boundary = self.config["imperfection_targets"]["coordinated_boundary_dates"]
        for qualified in [boundary["primary_target"], *boundary["dependent_targets"]]:
            table_name, field_name = qualified.split(".")
            values = {
                str(value).split("T", 1)[0]
                for value in tables[table_name][field_name]
                if not _is_blank(value)
            }
            if values & boundary_values:
                raise ValueError(
                    f"Base Logistics data already contains boundary values in {qualified}"
                )


def validate_logistics_generated_tables(
    generator: DeterministicGenerator,
    tables: dict[str, Any],
) -> None:
    """Run all immediate clean Logistics guards or raise on first failure."""

    LogisticsGeneratedTablesValidator(generator).validate(tables)


def _validate_shipment_status_dates(
    status: str,
    ship_date: date | None,
    delivery_date: date | None,
) -> None:
    requirements = {
        "Booked": (False, False),
        "InTransit": (True, False),
        "Delivered": (True, True),
        "Failed": (True, False),
        "Lost": (True, False),
    }
    requires_ship, requires_delivery = requirements[status]
    if (ship_date is not None) != requires_ship:
        raise ValueError(f"Shipment status {status} has invalid ship_date")
    if (delivery_date is not None) != requires_delivery:
        raise ValueError(f"Shipment status {status} has invalid delivery_date")


def _validate_fixed_decimal(
    value: Any,
    precision: int,
    scale: int,
    context: str,
) -> None:
    text = str(value)
    if re.fullmatch(rf"-?\d+\.\d{{{scale}}}", text) is None:
        raise ValueError(f"{context} must use fixed scale {scale}")
    try:
        decimal_value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"{context} contains an invalid decimal") from exc
    if len(decimal_value.as_tuple().digits) > precision:
        raise ValueError(f"{context} exceeds precision {precision}")


def _has_blanks(series: Any) -> bool:
    return bool(series.isna().any() or series.eq("").any())


def _is_blank(value: Any) -> bool:
    return value is None or value == "" or value != value


__all__ = [
    "LogisticsGeneratedTablesValidator",
    "validate_logistics_generated_tables",
]
