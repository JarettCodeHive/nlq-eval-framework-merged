"""Full relational and Logistics validation for final datasets."""

from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import time
from decimal import Decimal
from decimal import InvalidOperation
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.imperfections import count_from_pct
from generators.core.integrity import IntegrityCheckResult
from generators.core.integrity import assert_all_passed
from generators.core.integrity import classify_reference_values
from generators.core.integrity import empty_count
from generators.core.integrity import failed
from generators.core.integrity import non_empty_values
from generators.core.integrity import passed
from generators.core.schema_contract import primary_key_fields
from generators.logistics.config import load_logistics_config
from generators.logistics.config import settings_for_profile
from generators.logistics.imperfections import LogisticsImperfectionInjector
from generators.logistics.validators.config import validate_logistics_config


class LogisticsRelationalValidator:
    """Report complete relational and business validity for Logistics data."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "logistics":
            raise ValueError("LogisticsRelationalValidator only supports logistics")
        validate_logistics_config()
        self.generator = generator
        self.settings = generator.settings
        self.config = load_logistics_config()
        self.rules = self.config["generation_rules"]
        self.targets = self.config["imperfection_targets"]

    @classmethod
    def for_profile(cls, profile: str) -> "LogisticsRelationalValidator":
        """Create a relational validator from validated Logistics config."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def generate_and_validate(self) -> list[IntegrityCheckResult]:
        """Generate final imperfect Logistics tables and validate them."""

        tables = LogisticsImperfectionInjector(
            self.generator
        ).generate_imperfect_tables()
        return self.validate_tables(tables)

    def validate_tables(self, tables: dict[str, Any]) -> list[IntegrityCheckResult]:
        """Return every safely applicable Logistics integrity check."""

        results = self._validate_table_contract(tables)
        if any(not result.passed for result in results):
            return results

        column_results = self._validate_columns(tables)
        results.extend(column_results)
        if any(not result.passed for result in column_results):
            return results

        prerequisites: list[IntegrityCheckResult] = []
        prerequisites.extend(self._validate_row_counts(tables))
        prerequisites.extend(self._validate_keys(tables))
        prerequisites.extend(self._validate_required_fields(tables))
        prerequisites.extend(self._validate_physical_foreign_keys(tables))
        results.extend(prerequisites)
        if any(not result.passed for result in prerequisites):
            return results

        results.extend(self._validate_warehouse_classification(tables))
        results.extend(self._validate_domains(tables))
        results.extend(self._validate_decimal_fields(tables))
        results.extend(self._validate_shipments(tables))
        results.extend(self._validate_order_evidence(tables))
        results.extend(self._validate_duplicate_shipments(tables))
        results.extend(self._validate_inventory(tables))
        results.extend(self._validate_currency(tables))
        results.extend(self._validate_many_to_many(tables))
        return results

    def validate_or_raise(self, tables: dict[str, Any]) -> list[IntegrityCheckResult]:
        """Validate tables and raise one combined error for all failures."""

        results = self.validate_tables(tables)
        assert_all_passed(results)
        return results

    def _validate_table_contract(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        expected = self.settings.table_order
        actual = tuple(tables)
        results = [
            _result(
                "schema.table_order",
                actual == expected,
                "all tables present in dependency order",
                f"expected {list(expected)}, got {list(actual)}",
            )
        ]
        for table_name in expected:
            results.append(
                _result(
                    f"{table_name}.present",
                    table_name in tables,
                    "table present",
                    "table missing",
                )
            )
        extra = sorted(set(tables) - set(expected))
        results.append(
            _result(
                "schema.unexpected_tables",
                not extra,
                "no unexpected tables",
                f"unexpected tables: {extra}",
            )
        )
        return results

    def _validate_columns(self, tables: dict[str, Any]) -> list[IntegrityCheckResult]:
        results: list[IntegrityCheckResult] = []
        for table_name in self.settings.table_order:
            expected = [
                field["name"]
                for field in self.config["tables"][table_name]["fields"]
            ]
            actual = tables[table_name].columns.tolist()
            results.append(
                _result(
                    f"{table_name}.columns",
                    actual == expected,
                    "columns match config order",
                    f"expected {expected}, got {actual}",
                )
            )
        return results

    def _validate_row_counts(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        expected = {
            table_name: self.generator.row_count(table_name)
            for table_name in self.settings.table_order
        }
        expected["shipments"] += count_from_pct(
            expected["shipments"],
            float(self.settings.imperfections["duplicate_pct"]),
        )
        results = [
            _result(
                f"{table_name}.row_count",
                len(tables[table_name]) == row_count,
                f"final row count {row_count}",
                f"expected {row_count}, got {len(tables[table_name])}",
            )
            for table_name, row_count in expected.items()
        ]
        results.extend(
            _result(
                f"{table_name}.row_cap",
                len(tables[table_name]) <= self.settings.max_rows_per_table,
                f"within row cap {self.settings.max_rows_per_table}",
                f"{len(tables[table_name])} rows exceed row cap",
            )
            for table_name in self.settings.table_order
        )
        return results

    def _validate_keys(self, tables: dict[str, Any]) -> list[IntegrityCheckResult]:
        results: list[IntegrityCheckResult] = []
        for table_name in self.settings.table_order:
            table = tables[table_name]
            keys = primary_key_fields(self.config["tables"][table_name])
            blank_columns = [field for field in keys if empty_count(table[field])]
            duplicate_count = int(table.duplicated(keys).sum())
            results.append(
                _result(
                    f"{table_name}.primary_key",
                    not blank_columns and duplicate_count == 0,
                    "primary key populated and unique",
                    f"blank columns={blank_columns}, duplicates={duplicate_count}",
                )
            )
        return results

    def _validate_required_fields(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        results: list[IntegrityCheckResult] = []
        for table_name in self.settings.table_order:
            for field in self.config["tables"][table_name]["fields"]:
                if field["nullable"]:
                    continue
                blanks = empty_count(tables[table_name][field["name"]])
                results.append(
                    _result(
                        f"{table_name}.{field['name']}.not_null",
                        blanks == 0,
                        "no blank values",
                        f"{blanks} blank value(s)",
                    )
                )
        return results

    def _validate_physical_foreign_keys(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        results: list[IntegrityCheckResult] = []
        for relationship in self.config["physical_relationships"]:
            parent = non_empty_values(
                tables[relationship["parent_table"]][relationship["parent_field"]]
            )
            child = non_empty_values(
                tables[relationship["child_table"]][relationship["child_field"]]
            )
            invalid = sorted(child - parent)
            name = f"{relationship['child_table']}.{relationship['child_field']}.fk"
            results.append(
                _result(
                    name,
                    not invalid,
                    "all physical references valid",
                    f"invalid references: {invalid[:5]}",
                )
            )
        return results

    def _validate_warehouse_classification(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        orders = tables["orders"]
        target = self.targets["orphaned_order_warehouses"]
        namespace = int(target["namespace_base"])
        declared = {namespace + int(value) for value in orders["order_id"]}
        classification = classify_reference_values(
            orders["warehouse_id"],
            set(tables["warehouses"]["warehouse_id"]),
            declared,
        )
        expected_nulls = count_from_pct(
            self.generator.row_count("orders"),
            float(self.settings.imperfections["null_pct"]),
        )
        expected_orphans = count_from_pct(
            self.generator.row_count("orders"), float(target["rate_pct"])
        )
        formula_errors = 0
        valid_warehouses = set(tables["warehouses"]["warehouse_id"])
        for row in orders.itertuples(index=False):
            if _is_blank(row.warehouse_id) or row.warehouse_id in valid_warehouses:
                continue
            formula_errors += int(
                int(row.warehouse_id) != namespace + int(row.order_id)
            )
        return [
            _result(
                "orders.warehouse_id.null_count",
                classification.empty_count == expected_nulls,
                f"controlled NULL count {expected_nulls}",
                f"expected {expected_nulls}, got {classification.empty_count}",
            ),
            _result(
                "orders.warehouse_id.declared_orphans",
                classification.declared_orphan_count == expected_orphans,
                f"declared orphan count {expected_orphans}",
                f"expected {expected_orphans}, got "
                f"{classification.declared_orphan_count}",
            ),
            _result(
                "orders.warehouse_id.unexpected_orphans",
                classification.unexpected_orphan_count == 0,
                "no undeclared warehouse orphans",
                "unexpected values: "
                f"{sorted(classification.unexpected_orphan_values)[:5]}",
            ),
            _count_result("orders.warehouse_id.orphan_formula", formula_errors),
            _result(
                "orders.warehouse_id.valid_subset",
                classification.valid_count > 0,
                f"{classification.valid_count} valid warehouse assignment(s)",
                "no valid warehouse assignments remain",
            ),
        ]

    def _validate_domains(self, tables: dict[str, Any]) -> list[IntegrityCheckResult]:
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
        results: list[IntegrityCheckResult] = []
        for table_name, field_name, values_name in memberships:
            actual = non_empty_values(tables[table_name][field_name])
            invalid = sorted(actual - set(values[values_name]))
            results.append(
                _result(
                    f"{table_name}.{field_name}.domain",
                    not invalid,
                    "all populated values are configured",
                    f"unknown values: {invalid[:5]}",
                )
            )
        return results

    def _validate_decimal_fields(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        results: list[IntegrityCheckResult] = []
        for table_name in self.settings.table_order:
            for field in self.config["tables"][table_name]["fields"]:
                if field["type"] != "decimal":
                    continue
                invalid_format = 0
                invalid_range = 0
                for raw in tables[table_name][field["name"]]:
                    if _is_blank(raw):
                        continue
                    value = _fixed_decimal_or_none(
                        raw, int(field["precision"]), int(field["scale"])
                    )
                    if value is None:
                        invalid_format += 1
                        continue
                    if (table_name, field["name"]) == (
                        "warehouses",
                        "utilization_pct",
                    ):
                        invalid_range += not Decimal("0") <= value <= Decimal("100")
                    elif (table_name, field["name"]) == ("orders", "total_amount"):
                        invalid_range += value <= 0
                    else:
                        invalid_range += value < 0
                label = f"{table_name}.{field['name']}"
                results.append(_count_result(f"{label}.fixed_decimal", invalid_format))
                results.append(_count_result(f"{label}.range", invalid_range))
        return results

    def _validate_shipments(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        orders = tables["orders"].set_index("order_id")
        chronology = 0
        status_dates = 0
        for row in tables["shipments"].itertuples(index=False):
            order = orders.loc[row.order_id]
            order_date = _date(order["order_date"])
            order_created = datetime.fromisoformat(str(order["created_at"]))
            created = datetime.fromisoformat(str(row.created_at))
            shipped = _optional_date(row.ship_date)
            delivered = _optional_date(row.delivery_date)
            chronology += created < order_created
            chronology += shipped is not None and shipped < order_date
            chronology += delivered is not None and (
                shipped is None or delivered < shipped
            )
            chronology += shipped is not None and created > datetime.combine(
                shipped, time.max
            )
            status_dates += not _status_dates_valid(row.status, shipped, delivered)
        return [
            _temporal_result("shipments.chronology", chronology),
            _count_result("shipments.status_date_consistency", status_dates),
        ]

    def _validate_order_evidence(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        shipments = tables["shipments"]
        shipment_orders = set(shipments["order_id"])
        delivered_orders = set(
            shipments.loc[shipments["status"] == "Delivered", "order_id"]
        )
        evidence = self.config["business_mappings"]["order_status_evidence"]
        violations = 0
        for row in tables["orders"].itertuples(index=False):
            requirement = evidence[row.status]
            if requirement in {"shipment_required", "shipment_history_required"}:
                violations += row.order_id not in shipment_orders
            elif requirement == "delivered_shipment_required":
                violations += row.order_id not in delivered_orders
        unmatched = set(tables["orders"]["order_id"]) - shipment_orders
        return [
            _count_result("orders.shipment_evidence", violations),
            _result(
                "orders.without_shipments",
                bool(unmatched),
                f"{len(unmatched)} order(s) support LEFT-style analysis",
                "no unshipped orders remain",
            ),
        ]

    def _validate_duplicate_shipments(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        shipments = tables["shipments"]
        base_count = self.generator.row_count("shipments")
        base = shipments.iloc[:base_count]
        duplicates = shipments.iloc[base_count:]
        target = self.targets["near_duplicate_shipments"]
        keys = target["business_key_fields"]
        variation = set(target["variation_fields"])
        expected = count_from_pct(
            base_count, float(self.settings.imperfections["duplicate_pct"])
        )
        source_by_key = base.set_index(keys, drop=False)
        semantic_errors = 0
        for row in duplicates.itertuples(index=False):
            key = tuple(getattr(row, field) for field in keys)
            lookup: Any = key[0] if len(key) == 1 else key
            if lookup not in source_by_key.index:
                semantic_errors += 1
                continue
            source = source_by_key.loc[lookup]
            changed = {
                field for field in variation if getattr(row, field) != source[field]
            }
            unchanged_fields = set(shipments.columns) - variation - {"shipment_id"}
            semantic_errors += not changed or any(
                getattr(row, field) != source[field] for field in unchanged_fields
            )
        expected_ids = list(
            range(
                int(base["shipment_id"].max()) + 1,
                int(base["shipment_id"].max()) + expected + 1,
            )
        )
        duplicate_pairs = int(shipments.duplicated(keys).sum())
        return [
            _result(
                "shipments.controlled_duplicates",
                len(duplicates) == expected and duplicate_pairs == expected,
                f"controlled duplicate count {expected}",
                f"rows={len(duplicates)}, duplicate keys={duplicate_pairs}, "
                f"expected={expected}",
            ),
            _result(
                "shipments.duplicate_ids",
                duplicates["shipment_id"].tolist() == expected_ids,
                "duplicate IDs are fresh and sequential",
                "duplicate IDs are not fresh and sequential",
            ),
            _count_result("shipments.duplicate_semantics", semantic_errors),
        ]

    def _validate_inventory(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        inventory = tables["inventory"]
        pair_duplicates = int(
            inventory.duplicated(["warehouse_id", "product_sku"]).sum()
        )
        invalid_quantities = 0
        invalid_reorder = 0
        invalid_dates = 0
        below = False
        above = False
        for row in inventory.itertuples(index=False):
            quantity = int(row.quantity_on_hand)
            invalid_quantities += quantity < 0
            if not _is_blank(row.reorder_point):
                reorder = int(row.reorder_point)
                invalid_reorder += reorder < 0
                below = below or quantity < reorder
                above = above or quantity > reorder
            invalid_dates += datetime.fromisoformat(
                row.created_at
            ) > datetime.fromisoformat(row.last_updated_at)
        return [
            _count_result("inventory.logical_pair_unique", pair_duplicates),
            _count_result("inventory.quantity_non_negative", invalid_quantities),
            _count_result("inventory.reorder_non_negative", invalid_reorder),
            _temporal_result("inventory.update_chronology", invalid_dates),
            _result(
                "inventory.reorder_examples",
                below and above,
                "below- and above-reorder examples present",
                f"below={below}, above={above}",
            ),
        ]

    def _validate_currency(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        expected = self.config["business_mappings"]["currency_code"]
        return [
            _result(
                f"{table_name}.currency_code",
                set(tables[table_name]["currency_code"]) == {expected},
                f"all values use {expected}",
                f"unexpected currencies: "
                f"{sorted(set(tables[table_name]['currency_code']))}",
            )
            for table_name in ("carriers", "orders", "shipments")
        ]

    def _validate_many_to_many(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        shipments = tables["shipments"]
        carriers_per_order = shipments.groupby("order_id")["carrier_id"].nunique()
        orders_per_carrier = shipments.groupby("carrier_id")["order_id"].nunique()
        return [
            _result(
                "shipments.order_many_carriers",
                not carriers_per_order.empty and int(carriers_per_order.max()) >= 2,
                "at least one order uses multiple carriers",
                "no order uses multiple carriers",
            ),
            _result(
                "shipments.carrier_many_orders",
                not orders_per_carrier.empty and int(orders_per_carrier.max()) >= 2,
                "at least one carrier serves multiple orders",
                "no carrier serves multiple orders",
            ),
        ]


def _fixed_decimal_or_none(value: Any, precision: int, scale: int) -> Decimal | None:
    text = str(value)
    if "." not in text or len(text.rsplit(".", 1)[1]) != scale:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    if not amount.is_finite():
        return None
    digits = text.lstrip("-").replace(".", "").lstrip("0") or "0"
    return amount if len(digits) <= precision else None


def _status_dates_valid(
    status: str,
    ship_date: date | None,
    delivery_date: date | None,
) -> bool:
    expected = {
        "Booked": (False, False),
        "InTransit": (True, False),
        "Delivered": (True, True),
        "Failed": (True, False),
        "Lost": (True, False),
    }.get(status)
    return expected == (ship_date is not None, delivery_date is not None)


def _date(value: Any) -> date:
    return date.fromisoformat(str(value))


def _optional_date(value: Any) -> date | None:
    return None if _is_blank(value) else _date(value)


def _is_blank(value: Any) -> bool:
    return value is None or value == "" or value != value


def _result(
    check_name: str,
    condition: bool,
    passed_message: str,
    failed_message: str,
) -> IntegrityCheckResult:
    return passed(check_name, passed_message) if condition else failed(check_name, failed_message)


def _count_result(check_name: str, violation_count: int) -> IntegrityCheckResult:
    return _result(
        check_name,
        violation_count == 0,
        "no violations",
        f"{violation_count} violation(s)",
    )


def _temporal_result(check_name: str, violation_count: int) -> IntegrityCheckResult:
    return _result(
        check_name,
        violation_count == 0,
        "chronology valid",
        f"{violation_count} temporal violation(s)",
    )


__all__ = ["LogisticsRelationalValidator"]
