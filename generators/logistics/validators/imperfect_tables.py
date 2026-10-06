"""Immediate validation for controlled Logistics imperfection results."""

from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import time
from decimal import Decimal
from decimal import InvalidOperation
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.imperfections import count_from_pct
from generators.core.schema_contract import primary_key_fields
from generators.logistics.config import load_logistics_config
from generators.logistics.validators.distributed_tables import (
    validate_logistics_distributed_tables,
)


class LogisticsImperfectTablesValidator:
    """Validate exact Logistics defects and reject collateral mutations.

    This immediate gate checks table shape, fresh duplicate IDs, logical
    duplicate variation, exact missing/orphan/outlier rates, coordinated
    boundary paths, physical relationships, chronology, and optional drift
    from the distributed source. The broader release audit remains the
    responsibility of the later relational validator.
    """

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "logistics":
            raise ValueError(
                "LogisticsImperfectTablesValidator only supports logistics"
            )
        self.generator = generator
        self.settings = generator.settings
        self.config = self.settings.imperfections
        self.logistics_config = load_logistics_config()
        self.targets = self.logistics_config["imperfection_targets"]

    def validate(
        self,
        tables: dict[str, Any],
        source_tables: dict[str, Any] | None = None,
    ) -> None:
        """Validate exact target results and optional source preservation."""

        if source_tables is not None:
            validate_logistics_distributed_tables(self.generator, source_tables)
        self._validate_table_contracts(tables)
        self._validate_keys_and_required_fields(tables)
        self._validate_physical_relationships(tables)
        self._validate_near_duplicate_shipments(tables)
        self._validate_warehouse_imperfections(tables)
        self._validate_order_total_outliers(tables)
        self._validate_boundary_paths(tables)
        self._validate_shipment_chronology(tables)
        self._validate_currency_and_inventory(tables)
        if source_tables is not None:
            self._validate_source_preservation(tables, source_tables)

    def _validate_table_contracts(self, tables: dict[str, Any]) -> None:
        """Require signed columns and only declared shipment row growth."""

        if tuple(tables) != self.settings.table_order:
            raise ValueError("Imperfect Logistics table order differs from config")
        duplicate_count = self._expected_duplicate_count()
        for table_name in self.settings.table_order:
            table = tables[table_name]
            expected_columns = [
                field["name"]
                for field in self.logistics_config["tables"][table_name]["fields"]
            ]
            if table.columns.tolist() != expected_columns:
                raise ValueError(
                    f"{table_name} columns changed after Logistics imperfections"
                )
            expected_rows = self.generator.row_count(table_name)
            if table_name == "shipments":
                expected_rows += duplicate_count
            if len(table) != expected_rows:
                raise ValueError(
                    f"{table_name} imperfect row count differs from expected: "
                    f"expected {expected_rows}, got {len(table)}"
                )
            if len(table) > self.settings.max_rows_per_table:
                raise ValueError(f"{table_name} exceeds the configured row cap")

    def _validate_keys_and_required_fields(self, tables: dict[str, Any]) -> None:
        """Require populated unique PKs and all non-nullable fields."""

        for table_name in self.settings.table_order:
            table_config = self.logistics_config["tables"][table_name]
            table = tables[table_name]
            keys = primary_key_fields(table_config)
            if any(_blank_count(table[field]) for field in keys):
                raise ValueError(f"{table_name} contains blank primary keys")
            if table.duplicated(keys).any():
                raise ValueError(f"{table_name} contains duplicate primary keys")
            for field in table_config["fields"]:
                if not field["nullable"] and _blank_count(table[field["name"]]):
                    raise ValueError(
                        f"Required field contains blanks: "
                        f"{table_name}.{field['name']}"
                    )

    def _validate_physical_relationships(self, tables: dict[str, Any]) -> None:
        """Require every declared physical FK to resolve to a parent row."""

        for table_name, table_config in self.logistics_config["tables"].items():
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
                        f"{table_name}.{field['name']} contains physical orphans"
                    )

    def _validate_near_duplicate_shipments(
        self,
        tables: dict[str, Any],
    ) -> None:
        """Validate duplicate count, fresh IDs, shared keys, and variation."""

        shipments = tables["shipments"]
        base_count = self.generator.row_count("shipments")
        expected = self._expected_duplicate_count()
        base_rows = shipments.iloc[:base_count]
        duplicates = shipments.iloc[base_count:]
        target = self.targets["near_duplicate_shipments"]
        business_keys = target["business_key_fields"]
        variation_fields = set(target["variation_fields"])

        if len(duplicates) != expected:
            raise ValueError("Near-duplicate shipment count differs from config")
        expected_ids = list(
            range(
                int(base_rows["shipment_id"].max()) + 1,
                int(base_rows["shipment_id"].max()) + expected + 1,
            )
        )
        if duplicates["shipment_id"].tolist() != expected_ids:
            raise ValueError("Near-duplicate shipment IDs are not fresh and sequential")
        if base_rows.duplicated(business_keys).any():
            raise ValueError("Base shipment business keys are not unique")

        source_by_key = base_rows.set_index(business_keys, drop=False)
        unchanged = set(shipments.columns) - variation_fields - {"shipment_id"}
        boundary_dates = set(self.config["boundary_dates"])
        for row in duplicates.itertuples(index=False):
            key = tuple(getattr(row, field) for field in business_keys)
            lookup: Any = key[0] if len(key) == 1 else key
            if lookup not in source_by_key.index:
                raise ValueError("Near-duplicate shipment has no source business key")
            source = source_by_key.loc[lookup]
            if any(getattr(row, field) != source[field] for field in unchanged):
                raise ValueError(
                    "Near-duplicate shipment changed a non-variation field"
                )
            if not any(
                getattr(row, field) != source[field] for field in variation_fields
            ):
                raise ValueError("Near-duplicate shipment is byte-identical")
            if any(
                str(getattr(row, field)).split("T", 1)[0] in boundary_dates
                for field in ("ship_date", "delivery_date", "created_at")
                if not _is_blank(getattr(row, field))
            ):
                raise ValueError("Near-duplicate shipment overlaps a boundary path")

        actual = int(shipments.duplicated(business_keys).sum())
        if actual != expected:
            raise ValueError(
                "Shipment business-key duplicate count differs from config: "
                f"expected {expected}, got {actual}"
            )

    def _validate_warehouse_imperfections(self, tables: dict[str, Any]) -> None:
        """Require exact disjoint NULL and declared-orphan warehouse groups."""

        orders = tables["orders"]
        warehouse_ids = set(tables["warehouses"]["warehouse_id"])
        missing_target = self.targets["missing_order_warehouses"]
        orphan_target = self.targets["orphaned_order_warehouses"]
        field = missing_target["field"]
        missing = orders[orders[field].map(_is_blank)]
        orphaned = orders[
            orders[field].map(
                lambda value: not _is_blank(value) and value not in warehouse_ids
            )
        ]
        expected_missing = count_from_pct(
            self.generator.row_count("orders"), float(self.config["null_pct"])
        )
        expected_orphans = count_from_pct(
            self.generator.row_count("orders"), float(orphan_target["rate_pct"])
        )
        if len(missing) != expected_missing:
            raise ValueError(
                "orders.warehouse_id NULL count differs from configured rate"
            )
        if len(orphaned) != expected_orphans:
            raise ValueError(
                "orders.warehouse_id orphan count differs from configured rate"
            )
        namespace = int(orphan_target["namespace_base"])
        for row in orphaned.itertuples(index=False):
            if int(row.warehouse_id) != namespace + int(row.order_id):
                raise ValueError("An order uses an undeclared warehouse orphan")
        if set(missing["order_id"]) & set(orphaned["order_id"]):
            raise ValueError("Warehouse NULL and orphan selections overlap")

    def _validate_order_total_outliers(self, tables: dict[str, Any]) -> None:
        """Require exact fixed-scale outlier count and configured range."""

        target = self.targets["order_total_outliers"]
        minimum = Decimal(str(target["minimum_value"]))
        maximum = Decimal(str(target["maximum_value"]))
        clean_maximum = Decimal(
            str(self.logistics_config["distributions"]["order_total"]["max_amount"])
        )
        expected = count_from_pct(
            self.generator.row_count(target["table"]),
            float(self.config["outlier_pct"]),
        )
        outliers: list[Decimal] = []
        for value in tables[target["table"]][target["field"]]:
            amount = _require_decimal_scale(
                value,
                int(target["scale"]),
                f"{target['table']}.{target['field']}",
            )
            if amount >= minimum:
                if amount > maximum:
                    raise ValueError("An order-total outlier exceeds its maximum")
                outliers.append(amount)
            elif amount > clean_maximum:
                raise ValueError(
                    "An order total falls between clean and outlier ranges"
                )
        if len(outliers) != expected:
            raise ValueError(
                "Order-total outlier count differs from configured rate: "
                f"expected {expected}, got {len(outliers)}"
            )

    def _validate_boundary_paths(self, tables: dict[str, Any]) -> None:
        """Require one exact order and delivered-shipment path per boundary."""

        orders = tables["orders"]
        shipments = tables["shipments"]
        for boundary in self.config["boundary_dates"]:
            boundary_orders = orders[orders["order_date"] == boundary]
            if len(boundary_orders) != 1:
                raise ValueError(
                    f"orders.order_date boundary {boundary} must occur exactly once"
                )
            order = boundary_orders.iloc[0]
            if str(order["created_at"]).split("T", 1)[0] != boundary:
                raise ValueError("Boundary order creation date is not coordinated")
            path = shipments[
                (shipments["order_id"] == order["order_id"])
                & (shipments["status"] == "Delivered")
                & (shipments["ship_date"] == boundary)
                & (shipments["delivery_date"] == boundary)
                & shipments["created_at"].astype(str).str.startswith(boundary)
            ]
            if path.empty:
                raise ValueError(
                    f"Boundary {boundary} lacks a coordinated delivered shipment"
                )

    def _validate_shipment_chronology(self, tables: dict[str, Any]) -> None:
        """Require shipment status/date semantics and order-relative chronology."""

        orders = tables["orders"].set_index("order_id")
        for row in tables["shipments"].itertuples(index=False):
            order = orders.loc[row.order_id]
            order_date = date.fromisoformat(str(order["order_date"]))
            order_created = datetime.fromisoformat(str(order["created_at"]))
            created = datetime.fromisoformat(str(row.created_at))
            ship_date = date.fromisoformat(row.ship_date) if row.ship_date else None
            delivery_date = (
                date.fromisoformat(row.delivery_date) if row.delivery_date else None
            )
            if created < order_created:
                raise ValueError("Shipment creation precedes order creation")
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
            _validate_status_dates(row.status, ship_date, delivery_date)

    def _validate_currency_and_inventory(self, tables: dict[str, Any]) -> None:
        """Require unchanged USD semantics and valid inventory snapshots."""

        expected = self.logistics_config["business_mappings"]["currency_code"]
        for table_name in ("carriers", "orders", "shipments"):
            if set(tables[table_name]["currency_code"]) != {expected}:
                raise ValueError(f"{table_name} contains non-USD currency values")
        inventory = tables["inventory"]
        if inventory.duplicated(["warehouse_id", "product_sku"]).any():
            raise ValueError("Inventory contains duplicate warehouse/SKU pairs")
        for row in inventory.itertuples(index=False):
            if int(row.quantity_on_hand) < 0:
                raise ValueError("Inventory quantity cannot be negative")
            if datetime.fromisoformat(row.created_at) > datetime.fromisoformat(
                row.last_updated_at
            ):
                raise ValueError("Inventory update precedes creation")

    def _validate_source_preservation(
        self,
        tables: dict[str, Any],
        source_tables: dict[str, Any],
    ) -> None:
        """Allow base-row mutations only in declared imperfection targets."""

        if tuple(source_tables) != self.settings.table_order:
            raise ValueError("Source Logistics table order differs from config")
        mutable = _imperfection_mutable_fields(self.logistics_config)
        for table_name in self.settings.table_order:
            source = source_tables[table_name].reset_index(drop=True)
            result = tables[table_name].iloc[: len(source)].reset_index(drop=True)
            if source.columns.tolist() != result.columns.tolist():
                raise ValueError(
                    f"{table_name} columns changed during imperfection injection"
                )
            for column_name in source.columns:
                if column_name not in mutable[table_name] and not source[
                    column_name
                ].equals(result[column_name]):
                    raise ValueError(
                        f"{table_name}.{column_name} changed unexpectedly during "
                        "imperfection injection"
                    )

    def _expected_duplicate_count(self) -> int:
        return count_from_pct(
            self.generator.row_count("shipments"),
            float(self.config["duplicate_pct"]),
        )


def validate_logistics_imperfect_tables(
    generator: DeterministicGenerator,
    tables: dict[str, Any],
    source_tables: dict[str, Any] | None = None,
) -> None:
    """Run every immediate Logistics imperfection-result guard."""

    LogisticsImperfectTablesValidator(generator).validate(tables, source_tables)


def _imperfection_mutable_fields(
    config: dict[str, Any],
) -> dict[str, set[str]]:
    """Derive allowed base-row mutations from Logistics target configuration."""

    mutable = {table_name: set() for table_name in config["tables"]}
    targets = config["imperfection_targets"]
    for target_name in (
        "missing_order_warehouses",
        "orphaned_order_warehouses",
        "order_total_outliers",
    ):
        target = targets[target_name]
        mutable[target["table"]].add(target["field"])
    boundary = targets["coordinated_boundary_dates"]
    for qualified in [
        boundary["primary_target"],
        *boundary["dependent_targets"],
    ]:
        table_name, field_name = qualified.split(".", 1)
        mutable[table_name].add(field_name)
    return mutable


def _validate_status_dates(
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


def _require_decimal_scale(value: Any, scale: int, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} contains a non-decimal value") from exc
    if not parsed.is_finite() or parsed.as_tuple().exponent != -scale:
        raise ValueError(f"{label} does not use scale {scale}")
    return parsed


def _blank_count(series: Any) -> int:
    return int(series.isna().sum() + series.astype(str).eq("").sum())


def _is_blank(value: Any) -> bool:
    return value is None or value == "" or value != value


__all__ = [
    "LogisticsImperfectTablesValidator",
    "validate_logistics_imperfect_tables",
]
