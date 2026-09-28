"""Logistics release data-dictionary generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.manifest import build_distribution_metadata
from generators.logistics.config import load_logistics_config
from generators.logistics.config import settings_for_profile
from generators.logistics.validators.config import validate_logistics_config


TABLE_DESCRIPTIONS = {
    "carriers": (
        "Carrier reference dimension used for service-level, type, rate, and "
        "shipment-provider analysis."
    ),
    "warehouses": (
        "Warehouse reference dimension containing region, capacity, and "
        "utilization attributes."
    ),
    "orders": (
        "Customer-order fact table with an analytical, intentionally "
        "orphanable warehouse relationship."
    ),
    "shipments": (
        "Shipment relationship fact connecting orders and carriers while "
        "recording delivery status, dates, and cost."
    ),
    "inventory": (
        "Warehouse-product inventory snapshot fact at one row per warehouse "
        "and synthetic Logistics SKU."
    ),
}


FIELD_DESCRIPTIONS = {
    "carriers.carrier_id": "Unique carrier identifier.",
    "carriers.carrier_name": "Unique synthetic carrier display name.",
    "carriers.service_level": "Service speed offered by the carrier.",
    "carriers.carrier_type": "Optional transportation mode classification.",
    "carriers.base_rate": "Optional baseline carrier rate in USD.",
    "carriers.currency_code": "Carrier rate currency; always USD.",
    "carriers.is_active": "Whether the carrier is currently active.",
    "carriers.created_at": "Timestamp when the carrier record was created.",
    "warehouses.warehouse_id": "Unique warehouse identifier.",
    "warehouses.warehouse_name": "Unique synthetic distribution-center name.",
    "warehouses.region": "Optional geographic operating region.",
    "warehouses.country_code": "Optional country code compatible with region.",
    "warehouses.capacity_units": "Optional maximum storage capacity in units.",
    "warehouses.utilization_pct": "Optional utilized capacity percentage.",
    "warehouses.is_active": "Whether the warehouse is currently active.",
    "warehouses.created_at": "Timestamp when the warehouse record was created.",
    "orders.order_id": "Unique customer-order identifier.",
    "orders.warehouse_id": (
        "Warehouse assignment used as an analytical LEFT JOIN key; may be "
        "NULL or a declared orphan by design."
    ),
    "orders.customer_name": "Synthetic customer organization name.",
    "orders.order_date": "Calendar date on which the order was placed.",
    "orders.status": "Current order lifecycle status.",
    "orders.order_priority": "Operational fulfillment priority.",
    "orders.total_amount": "Positive order value in USD.",
    "orders.currency_code": "Order currency; always USD.",
    "orders.created_at": "Timestamp when the order record was created.",
    "shipments.shipment_id": "Unique physical shipment-row identifier.",
    "shipments.order_id": "Order fulfilled by the shipment.",
    "shipments.carrier_id": "Carrier responsible for the shipment.",
    "shipments.tracking_number": (
        "Shipment business identifier; shared by controlled near-duplicates."
    ),
    "shipments.ship_date": "Actual ship date; NULL while still booked.",
    "shipments.delivery_date": (
        "Actual delivery date; populated only for delivered shipments."
    ),
    "shipments.status": "Current shipment lifecycle status.",
    "shipments.shipping_cost": "Optional shipment cost in USD.",
    "shipments.currency_code": "Shipment cost currency; always USD.",
    "shipments.created_at": "Timestamp when the shipment record was created.",
    "inventory.inventory_id": "Unique physical inventory-row identifier.",
    "inventory.warehouse_id": "Warehouse holding the inventory snapshot.",
    "inventory.product_sku": "Synthetic Logistics product identifier.",
    "inventory.product_category": "Optional inventory category.",
    "inventory.quantity_on_hand": "Non-negative units currently available.",
    "inventory.reorder_point": "Optional non-negative replenishment threshold.",
    "inventory.last_updated_at": "Timestamp of the inventory snapshot update.",
    "inventory.created_at": "Timestamp when the inventory row was created.",
}


class LogisticsDataDictionaryGenerator:
    """Generate the Logistics dictionary from validated configuration."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "logistics":
            raise ValueError(
                "LogisticsDataDictionaryGenerator only supports logistics"
            )
        validate_logistics_config()
        self.generator = generator
        self.settings = generator.settings
        self.logistics_config = load_logistics_config()
        self._validate_description_coverage()

    @classmethod
    def for_profile(cls, profile: str) -> "LogisticsDataDictionaryGenerator":
        """Create a Logistics dictionary generator for a profile."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def generate_markdown(self) -> str:
        """Return deterministic Markdown for the Logistics data contract."""

        lines = [
            "# Logistics Data Dictionary",
            "",
            f"Dataset version: `{self.settings.dataset_version}`",
            "",
            f"Schema source: `{self.logistics_config['schema_source']}`",
            "",
            f"Fixed reference date: `{self.settings.reference_today.isoformat()}`",
            "",
            (
                "Logistics v1.0 is USD-only and contains deterministic synthetic "
                "test data. Currency conversion belongs to the Finance domain."
            ),
            "",
            "## Tables",
            "",
        ]
        for table_name in self.settings.table_order:
            lines.extend(self._table_section(table_name))
        lines.extend(self._domain_values_section())
        lines.extend(self._generation_rules_section())
        lines.extend(self._distribution_section())
        lines.extend(self._relationship_section())
        lines.extend(self._join_path_section())
        lines.extend(self._business_semantics_section())
        lines.extend(self._imperfection_section())
        return "\n".join(lines).rstrip() + "\n"

    def write_release_dictionary(self) -> Path:
        """Atomically write the dictionary into an unsealed full release."""

        if not self.settings.is_release_profile:
            raise ValueError(
                "Logistics release data-dictionary generation requires the full "
                f"profile; got profile={self.settings.profile}"
            )
        manifest_path = self.settings.output_path / "manifest.json"
        if manifest_path.exists():
            raise FileExistsError(
                "Refusing to modify immutable release after manifest exists: "
                f"{manifest_path}"
            )
        output_path = self.settings.output_path / "data_dictionary.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = output_path.with_suffix(".md.tmp")
        temporary_path.write_text(
            self.generate_markdown(),
            encoding="utf-8",
            newline="\n",
        )
        temporary_path.replace(output_path)
        return output_path

    def _validate_description_coverage(self) -> None:
        expected_fields = {
            f"{table_name}.{field['name']}"
            for table_name in self.settings.table_order
            for field in self.logistics_config["tables"][table_name]["fields"]
        }
        if set(FIELD_DESCRIPTIONS) != expected_fields:
            raise ValueError(
                "Logistics field-description coverage differs from config: "
                f"missing={sorted(expected_fields - set(FIELD_DESCRIPTIONS))}, "
                f"extra={sorted(set(FIELD_DESCRIPTIONS) - expected_fields)}"
            )
        if set(TABLE_DESCRIPTIONS) != set(self.settings.table_order):
            raise ValueError(
                "Logistics table-description coverage differs from table_order"
            )

    def _table_section(self, table_name: str) -> list[str]:
        table = self.logistics_config["tables"][table_name]
        lines = [
            f"### `{table_name}`",
            "",
            TABLE_DESCRIPTIONS[table_name],
            "",
            f"Role: `{table['role']}`",
            "",
            (
                f"Base row targets: dev `{table['row_targets']['dev']}`, "
                f"full `{table['row_targets']['full']}`."
            ),
            "",
            "| Column | Type | Nullable | Key role | References | Default | Business meaning | Generation behavior | NULL / imperfection behavior |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        lines.extend(self._field_row(table_name, field) for field in table["fields"])
        lines.append("")
        return lines

    def _field_row(self, table_name: str, field: dict[str, Any]) -> str:
        name = field["name"]
        reference = field.get("references")
        analytical = field.get("analytical_reference")
        if reference:
            reference_text = (
                f"`{reference['table']}.{reference['field']}` (physical FK)"
            )
        elif analytical:
            reference_text = (
                f"`{analytical['table']}.{analytical['field']}` "
                "(analytical only)"
            )
        else:
            reference_text = ""
        default = field.get("default", "")
        if isinstance(default, bool):
            default = str(default).lower()
        default_text = f"`{default}`" if default != "" else ""
        key = f"`{field['key']}`" if field.get("key") else ""
        return (
            f"| `{name}` | `{_type_text(field)}` | "
            f"`{str(field['nullable']).lower()}` | {key} | {reference_text} | "
            f"{default_text} | {FIELD_DESCRIPTIONS[f'{table_name}.{name}']} | "
            f"{_generation_text(field)} | {_imperfection_text(table_name, field)} |"
        )

    def _domain_values_section(self) -> list[str]:
        lines = ["## Allowed Domain Values", ""]
        for name, values in self.logistics_config["domain_values"].items():
            rendered = ", ".join(f"`{value}`" for value in values)
            lines.append(f"- {name.replace('_', ' ').title()}: {rendered}.")
        lines.append("")
        return lines

    def _generation_rules_section(self) -> list[str]:
        return [
            "## Base Generation Rules",
            "",
            "```json",
            json.dumps(self.logistics_config["generation_rules"], indent=2),
            "```",
            "",
        ]

    def _distribution_section(self) -> list[str]:
        metadata = build_distribution_metadata(
            self.logistics_config["distributions"],
            self.settings.distributions,
            self.logistics_config["distribution_targets"],
        )
        lines = [
            "## Distribution Configuration",
            "",
            "| Setting | Shared preset | Logistics overrides | Effective parameters | Targets |",
            "|---|---|---|---|---|",
        ]
        for key, specification in metadata.items():
            overrides = (
                _compact_json(specification["overrides"])
                if specification["overrides"]
                else "None"
            )
            targets = ", ".join(
                _distribution_target_text(target)
                for target in specification["targets"].values()
            )
            lines.append(
                f"| `{key}` | `{specification['preset']}` | {overrides} | "
                f"{_compact_json(specification['effective_parameters'])} | "
                f"{targets} |"
            )
        lines.append("")
        return lines

    def _relationship_section(self) -> list[str]:
        lines = [
            "## Relationships",
            "",
            "| Type | Parent | Child | Cardinality | Join type | Enforcement |",
            "|---|---|---|---|---|---|",
        ]
        for relationship in self.logistics_config["relationships"]:
            physical = relationship["relationship_type"] == "foreign_key"
            enforcement = (
                "Physical FK."
                if physical
                else "Analytical relationship; NULLs and declared orphans allowed."
            )
            lines.append(
                f"| `{relationship['relationship_type']}` | "
                f"`{relationship['parent_table']}.{relationship['parent_field']}` | "
                f"`{relationship['child_table']}.{relationship['child_field']}` | "
                f"`{relationship['cardinality']}` | "
                f"`{relationship['join_type']}` | {enforcement} |"
            )
        lines.extend(
            [
                "",
                (
                    "`shipments` provides the Order-to-Carrier many-to-many path: "
                    "an order may use multiple carriers and a carrier may serve "
                    "multiple orders."
                ),
                (
                    "`orders.warehouse_id` intentionally has no physical FK so "
                    "the LEFT JOIN can exercise NULL and declared-orphan orders."
                ),
                "",
            ]
        )
        return lines

    def _join_path_section(self) -> list[str]:
        lines = [
            "## Required Join Paths",
            "",
            "| ID | Tables | Join type | Condition | Required result |",
            "|---|---|---|---|---|",
        ]
        for path in self.logistics_config["join_path_requirements"]:
            tables = " -> ".join(path["tables"])
            lines.append(
                f"| `{path['id']}` | `{tables}` | `{path['join_type']}` | "
                f"`{path['join_condition']}` | `{path['required_result']}` |"
            )
        lines.append("")
        return lines

    def _business_semantics_section(self) -> list[str]:
        policy = self.logistics_config["decimal_policy"]
        return [
            "## Logistics Business Semantics",
            "",
            "- Shipment chronology: `order_date <= ship_date <= delivery_date` when the dates are populated.",
            "- `Booked` shipments have no ship or delivery date; only `Delivered` shipments have a delivery date.",
            "- Shipped, Delivered, and Returned orders retain the required shipment evidence; some Pending or Cancelled orders remain unshipped.",
            "- Inventory grain is one logical snapshot per `(warehouse_id, product_sku)`; SKUs do not reference Sales products.",
            "- Physical shipment rows use `COUNT(*)`; distinct business shipments use `COUNT(DISTINCT tracking_number)` when duplicate effects must be removed.",
            f"- Decimal rounding mode is `{policy['rounding_mode']}`; calculate first and round once at scale `{policy['stored_scale']}`.",
            "- Carrier rates, order totals, and shipment costs are USD-only; Logistics has no FX conversion path.",
            "- Binary floating-point arithmetic is prohibited for contracted decimal values.",
            "",
        ]

    def _imperfection_section(self) -> list[str]:
        config = self.settings.imperfections
        targets = self.logistics_config["imperfection_targets"]
        outlier = targets["order_total_outliers"]
        orphan = targets["orphaned_order_warehouses"]
        boundaries = ", ".join(
            f"`{value}`" for value in config["boundary_dates"]
        )
        return [
            "## Controlled Imperfections",
            "",
            f"- Near-duplicate shipments: `{config['duplicate_pct']}%` of base shipments receive fresh primary keys, retain `(order_id, carrier_id, tracking_number)`, and vary an approved field.",
            f"- Missing warehouse assignments: `{config['null_pct']}%` of base orders have an empty `warehouse_id`.",
            f"- Declared warehouse orphans: `{orphan['rate_pct']}%` of base orders use `{orphan['namespace_base']} + order_id`; these are expected analytical exceptions, not physical FK failures.",
            "- Missing and orphan warehouse selections are deterministic and disjoint.",
            f"- Order-total outliers: `{config['outlier_pct']}%` from `{outlier['minimum_value']}` through `{outlier['maximum_value']}` at scale `{outlier['scale']}`.",
            f"- Coordinated order and shipment boundary dates: {boundaries}.",
            "- Boundary injection preserves shipment chronology and status/date rules.",
            "",
            "Business-semantic NULLs such as negotiated carrier rates, unclassified attributes, pending shipment costs, and absent reorder points remain distinct from controlled NULL-rate targets.",
            "",
        ]


def _type_text(field: dict[str, Any]) -> str:
    if field["type"] in {"varchar", "char"}:
        return f"{field['type']}({field['max_length']})"
    if field["type"] == "decimal":
        return f"decimal({field['precision']},{field['scale']})"
    return str(field["type"])


def _generation_text(field: dict[str, Any]) -> str:
    source = field.get("synthetic_source", "deterministic entity identifier")
    distribution = (
        f"; distribution `{field['distribution']}`"
        if field.get("distribution")
        else ""
    )
    return f"Source `{source}`{distribution}."


def _imperfection_text(table_name: str, field: dict[str, Any]) -> str:
    qualified = f"{table_name}.{field['name']}"
    behavior: list[str] = []
    if qualified == "shipments.shipment_id":
        behavior.append("Near-duplicate rows receive fresh identifiers.")
    targets = field.get("imperfection_targets", [])
    if field.get("imperfection_target"):
        targets = [*targets, field["imperfection_target"]]
    if targets:
        rendered = ", ".join(f"`{target}`" for target in targets)
        behavior.append(f"Controlled target(s): {rendered}.")
    if field.get("null_semantics"):
        behavior.append(f"NULL meaning: {field['null_semantics']}")
    elif field["nullable"] and not behavior:
        behavior.append("Optional business attribute; not a controlled NULL target.")
    return " ".join(behavior) or "Not an imperfection target."


def _compact_json(value: Any) -> str:
    return f"`{json.dumps(value, sort_keys=True, separators=(',', ':'))}`"


def _distribution_target_text(target: dict[str, Any]) -> str:
    if "targets" in target:
        return ", ".join(f"`{value}`" for value in target["targets"])
    if "field" in target:
        return f"`{target['table']}.{target['field']}`"
    if "group_by" in target:
        return f"`{target['table']}` grouped by `{target['group_by']}`"
    return f"`{target['table']}`"


__all__ = [
    "FIELD_DESCRIPTIONS",
    "LogisticsDataDictionaryGenerator",
    "TABLE_DESCRIPTIONS",
]
