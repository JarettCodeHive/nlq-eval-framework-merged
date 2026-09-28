"""Distribution-specific validation for generated Logistics tables."""

from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from decimal import Decimal
from decimal import InvalidOperation
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.schema_contract import primary_key_fields
from generators.logistics.config import load_logistics_config
from generators.logistics.validators.generated_tables import (
    validate_logistics_generated_tables,
)


class LogisticsDistributedTablesValidator:
    """Validate realistic Logistics distributions and source preservation.

    The clean-table guard first proves the shared schema, key, relationship,
    chronology, currency, inventory, and clean-stage contracts. This validator
    then verifies bounded Pareto amounts, exact non-uniform shipment frequency,
    observable Gaussian date clustering, configured date windows, and that only
    fields declared by the distribution configuration changed from the source.
    """

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "logistics":
            raise ValueError(
                "LogisticsDistributedTablesValidator only supports logistics"
            )
        self.generator = generator
        self.settings = generator.settings
        self.config = load_logistics_config()
        self.rules = self.config["generation_rules"]

    def validate(
        self,
        tables: dict[str, Any],
        source_tables: dict[str, Any] | None = None,
    ) -> None:
        """Validate distributed tables and optionally compare their source."""

        validate_logistics_generated_tables(self.generator, tables)
        self._validate_order_total_distribution(tables)
        self._validate_shipment_frequency(tables, source_tables)
        self._validate_date_clustering(tables)
        self._validate_distribution_date_windows(tables)
        if source_tables is not None:
            self._validate_source_preservation(tables, source_tables)

    def _validate_order_total_distribution(
        self,
        tables: dict[str, Any],
    ) -> None:
        """Require fixed-scale bounded amounts with an observable upper tail."""

        target = self.config["distribution_targets"]["order_total"]
        spec = self.settings.distributions[target["settings_key"]]
        minimum = Decimal(str(spec["min_amount"]))
        maximum = Decimal(str(spec["max_amount"]))
        scale = int(spec["scale"])
        label = f"{target['table']}.{target['field']}"
        values = [
            _parse_fixed_decimal(value, scale, label)
            for value in tables[target["table"]][target["field"]]
        ]
        if any(value < minimum or value > maximum for value in values):
            raise ValueError(f"{label} is outside configured distribution bounds")

        if len(values) >= 5:
            ordered = sorted(values)
            median = ordered[len(ordered) // 2]
            lower_quarter_limit = minimum + ((maximum - minimum) / Decimal(4))
            if median >= lower_quarter_limit or ordered[-1] <= median * Decimal(2):
                raise ValueError(f"{label} does not show an observable Pareto tail")

    def _validate_shipment_frequency(
        self,
        tables: dict[str, Any],
        source_tables: dict[str, Any] | None,
    ) -> None:
        """Require exact reconciliation and preserved shipment-bearing orders."""

        target = self.config["distribution_targets"]["shipment_frequency"]
        shipments = tables[target["table"]]
        group_by = target["group_by"]
        expected_rows = self.generator.row_count(target["table"])
        frequencies = shipments.groupby(group_by).size()
        if len(shipments) != expected_rows or int(frequencies.sum()) != expected_rows:
            raise ValueError(
                "Shipment frequencies do not reconcile to configured row target"
            )
        if frequencies.empty or int(frequencies.min()) < 1:
            raise ValueError("Distributed shipments lack covered order groups")
        if len(frequencies) > 1 and int(frequencies.nunique()) == 1:
            raise ValueError("Shipment frequencies do not vary between orders")

        if source_tables is not None and target["preserve_unshipped_orders"]:
            source_groups = set(source_tables[target["table"]][group_by])
            if set(frequencies.index) != source_groups:
                raise ValueError(
                    "Distribution changed the set of shipment-bearing orders"
                )

    def _validate_date_clustering(self, tables: dict[str, Any]) -> None:
        """Require order dates to exhibit the configured mixture centers."""

        spec = self.settings.distributions["date_clustering"]
        start = date.fromisoformat(
            self.rules["date_windows"]["order_activity_start"]
        )
        end = self.settings.reference_today - timedelta(days=45)
        span = (end - start).days
        if span <= 0:
            raise ValueError("Configured Logistics order-date window is invalid")

        normalized = [
            (date.fromisoformat(str(value)) - start).days / span
            for value in tables["orders"]["order_date"]
        ]
        tolerance = 3 * float(spec["std_fraction"])
        centers = [float(value) for value in spec["component_centers"]]
        clustered = sum(
            min(abs(value - center) for center in centers) <= tolerance
            for value in normalized
        )
        if normalized and clustered / len(normalized) < 0.8:
            raise ValueError(
                "orders.order_date does not show configured Gaussian clustering"
            )

    def _validate_distribution_date_windows(
        self,
        tables: dict[str, Any],
    ) -> None:
        """Require distributed business dates to remain in configured windows."""

        reference = self.settings.reference_today
        order_start = date.fromisoformat(
            self.rules["date_windows"]["order_activity_start"]
        )
        inventory_start = date.fromisoformat(
            self.rules["date_windows"]["inventory_activity_start"]
        )
        _assert_date_window(
            tables["orders"]["order_date"],
            order_start,
            reference - timedelta(days=45),
            "orders.order_date",
        )
        _assert_date_window(
            tables["shipments"]["ship_date"],
            order_start,
            reference - timedelta(days=31),
            "shipments.ship_date",
            allow_blank=True,
        )
        _assert_date_window(
            tables["shipments"]["delivery_date"],
            order_start,
            reference - timedelta(days=1),
            "shipments.delivery_date",
            allow_blank=True,
        )
        _assert_timestamp_window(
            tables["inventory"]["last_updated_at"],
            inventory_start,
            reference,
            "inventory.last_updated_at",
        )

    def _validate_source_preservation(
        self,
        tables: dict[str, Any],
        source_tables: dict[str, Any],
    ) -> None:
        """Reject row, key, or undeclared field drift from the clean source."""

        if tuple(source_tables) != self.settings.table_order:
            raise ValueError("Source Logistics table order differs from config")
        mutable = _distribution_mutable_fields(self.config)
        for table_name in self.settings.table_order:
            source = source_tables[table_name]
            distributed = tables[table_name]
            if source.columns.tolist() != distributed.columns.tolist():
                raise ValueError(
                    f"{table_name} columns changed during distribution application"
                )
            if len(source) != len(distributed):
                raise ValueError(
                    f"{table_name} row count changed during distribution application"
                )
            for key in primary_key_fields(self.config["tables"][table_name]):
                if not source[key].equals(distributed[key]):
                    raise ValueError(
                        f"{table_name}.{key} changed during distribution application"
                    )
            for column_name in source.columns:
                if column_name not in mutable[table_name] and not source[
                    column_name
                ].equals(distributed[column_name]):
                    raise ValueError(
                        f"{table_name}.{column_name} changed unexpectedly during "
                        "distribution application"
                    )


def validate_logistics_distributed_tables(
    generator: DeterministicGenerator,
    tables: dict[str, Any],
    source_tables: dict[str, Any] | None = None,
) -> None:
    """Run every immediate distributed Logistics guard."""

    LogisticsDistributedTablesValidator(generator).validate(tables, source_tables)


def _distribution_mutable_fields(
    config: dict[str, Any],
) -> dict[str, set[str]]:
    """Derive direct distribution targets and chronology dependencies."""

    mutable = {table_name: set() for table_name in config["tables"]}
    date_target_tables: set[str] = set()
    for target in config["distribution_targets"].values():
        table_name = target.get("table")
        field_name = target.get("field")
        if table_name in mutable and field_name is not None:
            mutable[table_name].add(field_name)
        if table_name in mutable:
            group_by = target.get("group_by")
            if group_by is not None:
                mutable[table_name].add(group_by)
            mutable[table_name].update(target.get("dependent_fields", []))
        for dotted_target in target.get("targets", []):
            dotted_table, dotted_field = dotted_target.split(".", 1)
            mutable[dotted_table].add(dotted_field)
            date_target_tables.add(dotted_table)

    for table_name in date_target_tables:
        if any(
            field["name"] == "created_at"
            for field in config["tables"][table_name]["fields"]
        ):
            mutable[table_name].add("created_at")
    return mutable


def _parse_fixed_decimal(value: Any, scale: int, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} contains a non-decimal value") from exc
    if not parsed.is_finite() or parsed.as_tuple().exponent != -scale:
        raise ValueError(f"{label} does not use scale {scale}")
    return parsed


def _assert_date_window(
    values: Any,
    start: date,
    end: date,
    label: str,
    allow_blank: bool = False,
) -> None:
    for value in values:
        if allow_blank and value == "":
            continue
        parsed = date.fromisoformat(str(value))
        if parsed < start or parsed > end:
            raise ValueError(f"{label} is outside its distribution window")


def _assert_timestamp_window(
    values: Any,
    start: date,
    end: date,
    label: str,
) -> None:
    lower = datetime.combine(start, time.min)
    upper = datetime.combine(end, time.max)
    for value in values:
        parsed = datetime.fromisoformat(str(value))
        if parsed < lower or parsed > upper:
            raise ValueError(f"{label} is outside its distribution window")


__all__ = [
    "LogisticsDistributedTablesValidator",
    "validate_logistics_distributed_tables",
]
