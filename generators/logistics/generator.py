"""Logistics dataframe contracts and deterministic base generation surface."""

from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
import re
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.base import validate_column_contracts
from generators.core.distributions import format_fixed_decimal
from generators.core.progress import ProgressReporter
from generators.core.temporal import random_date_inclusive
from generators.core.temporal import random_datetime_inclusive
from generators.logistics.config import load_logistics_config
from generators.logistics.config import settings_for_profile
from generators.logistics.validators.config import validate_logistics_config
from generators.logistics.validators.generated_tables import (
    validate_logistics_generated_tables,
)


def build_logistics_column_contracts(
    logistics_config: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    """Build ordered table and column contracts from Logistics configuration.

    The assembled schema component is the sole source of dataframe table and
    column order. Base generation and all later distribution, imperfection,
    validation, export, dictionary, and manifest stages therefore share one
    contract aligned with the canonical DDL and CSV header specification.
    """

    config = (
        logistics_config if logistics_config is not None else load_logistics_config()
    )
    contracts = {
        table_name: [field["name"] for field in config["tables"][table_name]["fields"]]
        for table_name in config["table_order"]
    }
    validate_column_contracts(
        config["table_order"],
        config["tables"],
        contracts,
    )
    return contracts


LOGISTICS_COLUMN_CONTRACTS = build_logistics_column_contracts()


class LogisticsBaseEntityGenerator:
    """Generate clean deterministic Logistics tables.

    The generator creates carriers, warehouses, orders, shipments, and
    inventory snapshots from named seeded streams and config-owned values. Base
    rows preserve every physical relationship, use valid warehouse assignments,
    maintain shipment status/date chronology, create the required order/carrier
    many-to-many pattern, and keep shipment business keys and inventory logical
    keys unique.

    Controlled warehouse NULLs and orphans, duplicate shipments, amount
    outliers, and boundary dates are deliberately excluded from this stage.
    """

    def __init__(
        self,
        generator: DeterministicGenerator,
        progress: ProgressReporter | None = None,
    ) -> None:
        if generator.settings.domain != "logistics":
            raise ValueError(
                "LogisticsBaseEntityGenerator only supports the logistics domain"
            )
        validate_logistics_config()
        self.generator = generator
        self.settings = generator.settings
        self.logistics_config = load_logistics_config()
        self.rules = self.logistics_config["generation_rules"]
        self.progress = progress

    @classmethod
    def for_profile(
        cls,
        profile: str,
        progress: ProgressReporter | None = None,
    ) -> "LogisticsBaseEntityGenerator":
        """Create a validated Logistics generator for one profile."""

        return cls(
            DeterministicGenerator(settings_for_profile(profile)),
            progress=progress,
        )

    def generate_tables(self) -> dict[str, Any]:
        """Generate all clean Logistics tables in dependency-safe order."""

        carriers = self._generate_and_report("carriers", self.generate_carriers)
        warehouses = self._generate_and_report("warehouses", self.generate_warehouses)
        orders = self._generate_and_report(
            "orders", lambda: self.generate_orders(warehouses)
        )
        shipments = self._generate_and_report(
            "shipments", lambda: self.generate_shipments(orders, carriers)
        )
        inventory = self._generate_and_report(
            "inventory", lambda: self.generate_inventory(warehouses)
        )
        tables = {
            "carriers": carriers,
            "warehouses": warehouses,
            "orders": orders,
            "shipments": shipments,
            "inventory": inventory,
        }
        self._report("Validating base Logistics tables")
        validate_logistics_generated_tables(self.generator, tables)
        self._report("Base Logistics generation complete")
        return tables

    def generate_carriers(self) -> Any:
        """Generate carrier reference rows with fixed-scale USD base rates."""

        pd = _require_pandas()
        count = self.generator.row_count("carriers")
        rng = self.generator.rng_for("logistics:carriers:attributes")
        date_rng = self.generator.rng_for("logistics:carriers:created_at")
        values = self.logistics_config["domain_values"]
        rules = self.rules["carriers"]
        ids = self.generator.make_integer_ids(count)
        service_levels = _choice_with_coverage(
            rng, values["service_levels"], count, required=values["service_levels"]
        )
        carrier_types = _choice_with_coverage(
            rng, values["carrier_types"], count, required=values["carrier_types"]
        )
        _apply_missing_values(
            rng, carrier_types, float(rules["missing_type_probability"])
        )
        rates = _uniform_decimal_strings(rng, count, rules["base_rate"])
        _apply_missing_values(rng, rates, float(rules["missing_base_rate_probability"]))
        prefixes = values["carrier_name_prefixes"]
        start = _configured_datetime(self.rules["date_windows"]["entity_created_start"])
        end = _reference_datetime(self.settings.reference_today)
        return pd.DataFrame(
            {
                "carrier_id": ids,
                "carrier_name": [
                    self.logistics_config["business_mappings"]["synthetic_names"][
                        "carrier_name"
                    ]["format"].format(
                        prefix=prefixes[(carrier_id - 1) % len(prefixes)],
                        carrier_id=carrier_id,
                    )
                    for carrier_id in ids
                ],
                "service_level": service_levels,
                "carrier_type": carrier_types,
                "base_rate": rates,
                "currency_code": ["USD"] * count,
                "is_active": rng.choice(
                    [True, False],
                    size=count,
                    p=[
                        float(rules["active_probability"]),
                        1 - float(rules["active_probability"]),
                    ],
                ).tolist(),
                "created_at": _random_timestamp_strings(date_rng, count, start, end),
            },
            columns=LOGISTICS_COLUMN_CONTRACTS["carriers"],
        )

    def generate_warehouses(self) -> Any:
        """Generate warehouse rows with consistent region/country assignments."""

        pd = _require_pandas()
        count = self.generator.row_count("warehouses")
        rng = self.generator.rng_for("logistics:warehouses:attributes")
        date_rng = self.generator.rng_for("logistics:warehouses:created_at")
        values = self.logistics_config["domain_values"]
        rules = self.rules["warehouses"]
        mappings = self.logistics_config["business_mappings"]
        ids = self.generator.make_integer_ids(count)
        regions = _choice_with_coverage(
            rng, values["regions"], count, required=values["regions"]
        )
        country_codes = [
            str(rng.choice(mappings["region_country_codes"][region]))
            for region in regions
        ]
        missing_region_count = _count_probability(
            count, float(rules["missing_region_probability"])
        )
        if missing_region_count:
            positions = rng.choice(count, size=missing_region_count, replace=False)
            for position in positions:
                regions[int(position)] = ""
                country_codes[int(position)] = ""
        country_candidates = [
            position for position, region in enumerate(regions) if region
        ]
        missing_country_count = min(
            _count_probability(count, float(rules["missing_country_probability"])),
            len(country_candidates),
        )
        if missing_country_count:
            positions = rng.choice(
                country_candidates,
                size=missing_country_count,
                replace=False,
            )
            for position in positions:
                country_codes[int(position)] = ""
        capacities: list[int | str] = _uniform_integers(
            rng, count, rules["capacity_units"]
        )
        _apply_missing_values(
            rng, capacities, float(rules["missing_capacity_probability"])
        )
        utilization: list[str] = _uniform_unit_decimal_strings(
            rng, count, rules["utilization_pct"]
        )
        _apply_missing_values(
            rng, utilization, float(rules["missing_utilization_probability"])
        )
        prefixes = values["warehouse_name_prefixes"]
        start = _configured_datetime(self.rules["date_windows"]["entity_created_start"])
        end = _reference_datetime(self.settings.reference_today)
        return pd.DataFrame(
            {
                "warehouse_id": ids,
                "warehouse_name": [
                    mappings["synthetic_names"]["warehouse_name"]["format"].format(
                        prefix=prefixes[(warehouse_id - 1) % len(prefixes)],
                        warehouse_id=warehouse_id,
                    )
                    for warehouse_id in ids
                ],
                "region": regions,
                "country_code": country_codes,
                "capacity_units": capacities,
                "utilization_pct": utilization,
                "is_active": rng.choice(
                    [True, False],
                    size=count,
                    p=[
                        float(rules["active_probability"]),
                        1 - float(rules["active_probability"]),
                    ],
                ).tolist(),
                "created_at": _random_timestamp_strings(date_rng, count, start, end),
            },
            columns=LOGISTICS_COLUMN_CONTRACTS["warehouses"],
        )

    def generate_orders(self, warehouses: Any) -> Any:
        """Generate clean orders with valid populated warehouse assignments."""

        pd = _require_pandas()
        count = self.generator.row_count("orders")
        rng = self.generator.rng_for("logistics:orders:attributes")
        date_rng = self.generator.rng_for("logistics:orders:dates")
        fake = self.generator.faker_for("logistics:orders:faker")
        values = self.logistics_config["domain_values"]
        rules = self.rules["orders"]
        ids = self.generator.make_integer_ids(count)
        warehouse_ids = [int(value) for value in warehouses["warehouse_id"].tolist()]
        statuses = _choice_with_coverage(
            rng,
            values["order_statuses"],
            count,
            weights=rules["status_weights"],
            required=rules["required_status_coverage"],
        )
        unshipped_count = max(1, round(count * rules["unshipped_order_fraction"]))
        optional_positions = [
            position
            for position, status in enumerate(statuses)
            if status in {"Pending", "Cancelled"}
        ]
        for position in range(len(optional_positions), unshipped_count):
            statuses[position] = "Pending" if position % 2 == 0 else "Cancelled"
        priorities = _choice_with_coverage(
            rng,
            values["order_priorities"],
            count,
            weights=rules["priority_weights"],
            required=rules["required_priority_coverage"],
        )
        start = date.fromisoformat(self.rules["date_windows"]["order_activity_start"])
        end = self.settings.reference_today - timedelta(days=45)
        order_dates = [
            random_date_inclusive(date_rng, start, end) for _ in range(count)
        ]
        created_start = _configured_datetime(
            self.rules["date_windows"]["entity_created_start"]
        )
        created_at = [
            _format_timestamp(
                random_datetime_inclusive(
                    date_rng,
                    created_start,
                    datetime.combine(order_date, time(23, 59, 59)),
                )
            )
            for order_date in order_dates
        ]
        amount_spec = self.logistics_config["distributions"]["order_total"]
        amounts = _uniform_decimal_strings(
            rng,
            count,
            {
                "minimum_amount": amount_spec["min_amount"],
                "maximum_amount": amount_spec["max_amount"],
                "scale": amount_spec["scale"],
            },
        )
        return pd.DataFrame(
            {
                "order_id": ids,
                "warehouse_id": rng.choice(warehouse_ids, size=count).astype(int),
                "customer_name": [
                    _synthetic_customer_name(fake, order_id) for order_id in ids
                ],
                "order_date": [value.isoformat() for value in order_dates],
                "status": statuses,
                "order_priority": priorities,
                "total_amount": amounts,
                "currency_code": ["USD"] * count,
                "created_at": created_at,
            },
            columns=LOGISTICS_COLUMN_CONTRACTS["orders"],
        )

    def generate_shipments(self, orders: Any, carriers: Any) -> Any:
        """Generate shipment facts linking valid orders and carriers.

        Every shipment uses a unique base tracking number. Orders requiring
        shipment evidence receive at least one row, a deterministic subset of
        optional orders remains unshipped, and seeded extra rows establish both
        sides of the implicit order/carrier many-to-many relationship.
        """

        pd = _require_pandas()
        count = self.generator.row_count("shipments")
        rng = self.generator.rng_for("logistics:shipments:relationships")
        date_rng = self.generator.rng_for("logistics:shipments:dates")
        rules = self.rules["shipments"]
        order_records = orders.set_index("order_id").to_dict("index")
        optional_ids = sorted(
            int(order_id)
            for order_id, row in order_records.items()
            if row["status"] in {"Pending", "Cancelled"}
        )
        unshipped_count = max(
            1,
            round(len(orders) * self.rules["orders"]["unshipped_order_fraction"]),
        )
        unshipped_ids = set(optional_ids[:unshipped_count])
        eligible_ids = [
            int(order_id)
            for order_id in orders["order_id"].tolist()
            if int(order_id) not in unshipped_ids
        ]
        if count < len(eligible_ids):
            raise ValueError(
                "Shipment target cannot cover every shipment-bearing order"
            )
        selected_orders = list(eligible_ids)
        remaining = count - len(selected_orders)
        if remaining:
            selected_orders.extend(
                int(value)
                for value in rng.choice(eligible_ids, size=remaining, replace=True)
            )
        carrier_ids = [int(value) for value in carriers["carrier_id"].tolist()]
        selected_carriers = [
            carrier_ids[position % len(carrier_ids)]
            for position in range(len(eligible_ids))
        ]
        if remaining:
            selected_carriers.extend(
                int(value)
                for value in rng.choice(carrier_ids, size=remaining, replace=True)
            )
            selected_orders[len(eligible_ids)] = eligible_ids[0]
            selected_carriers[len(eligible_ids)] = carrier_ids[1 % len(carrier_ids)]

        statuses: list[str] = []
        ship_dates: list[str] = []
        delivery_dates: list[str] = []
        created_at: list[str] = []
        costs = _uniform_decimal_strings(rng, count, rules["shipping_cost"])
        _apply_missing_values(rng, costs, float(rules["missing_cost_probability"]))
        seen_orders: set[int] = set()
        for order_id in selected_orders:
            order = order_records[order_id]
            first_for_order = order_id not in seen_orders
            seen_orders.add(order_id)
            status = _shipment_status_for_order(
                rng,
                str(order["status"]),
                first_for_order,
                rules["status_weights"],
            )
            ship_date, delivery_date = _shipment_dates(
                date_rng,
                date.fromisoformat(str(order["order_date"])),
                status,
                self.settings.reference_today,
                rules,
            )
            event_end = (
                datetime.combine(
                    date.fromisoformat(ship_date),
                    time(23, 59, 59),
                )
                if ship_date
                else _reference_datetime(
                    self.settings.reference_today - timedelta(days=1)
                )
            )
            creation_start = datetime.fromisoformat(str(order["created_at"]))
            statuses.append(status)
            ship_dates.append(ship_date)
            delivery_dates.append(delivery_date)
            created_at.append(
                _format_timestamp(
                    random_datetime_inclusive(date_rng, creation_start, event_end)
                )
            )
        ids = self.generator.make_integer_ids(count)
        tracking_format = self.logistics_config["business_mappings"]["tracking_number"][
            "format"
        ]
        return pd.DataFrame(
            {
                "shipment_id": ids,
                "order_id": selected_orders,
                "carrier_id": selected_carriers,
                "tracking_number": [
                    tracking_format.format(shipment_id=shipment_id)
                    for shipment_id in ids
                ],
                "ship_date": ship_dates,
                "delivery_date": delivery_dates,
                "status": statuses,
                "shipping_cost": costs,
                "currency_code": ["USD"] * count,
                "created_at": created_at,
            },
            columns=LOGISTICS_COLUMN_CONTRACTS["shipments"],
        )

    def generate_inventory(self, warehouses: Any) -> Any:
        """Generate unique warehouse/SKU inventory snapshots."""

        pd = _require_pandas()
        count = self.generator.row_count("inventory")
        rng = self.generator.rng_for("logistics:inventory:attributes")
        date_rng = self.generator.rng_for("logistics:inventory:dates")
        rules = self.rules["inventory"]
        warehouse_ids = [int(value) for value in warehouses["warehouse_id"].tolist()]
        ids = self.generator.make_integer_ids(count)
        selected_warehouses = [
            warehouse_ids[position % len(warehouse_ids)] for position in range(count)
        ]
        sku_format = self.logistics_config["business_mappings"]["product_sku"]["format"]
        skus = [
            sku_format.format(sku_id=(position // len(warehouse_ids)) + 1)
            for position in range(count)
        ]
        categories = _choice_with_coverage(
            rng,
            self.logistics_config["domain_values"]["product_categories"],
            count,
            required=self.logistics_config["domain_values"]["product_categories"],
        )
        _apply_missing_values(
            rng, categories, float(rules["missing_category_probability"])
        )
        quantities: list[int | str] = _uniform_integers(
            rng, count, rules["quantity_on_hand"]
        )
        reorder_points: list[int | str] = _uniform_integers(
            rng, count, rules["reorder_point"]
        )
        if count >= 2:
            quantities[0], reorder_points[0] = 10, 100
            quantities[1], reorder_points[1] = 500, 100
        _apply_missing_values(
            rng,
            reorder_points,
            float(rules["missing_reorder_point_probability"]),
            protected_positions={0, 1},
        )
        created_start = _configured_datetime(
            self.rules["date_windows"]["entity_created_start"]
        )
        inventory_start = _configured_datetime(
            self.rules["date_windows"]["inventory_activity_start"]
        )
        reference = _reference_datetime(self.settings.reference_today)
        created_at: list[str] = []
        updated_at: list[str] = []
        for _ in range(count):
            created = random_datetime_inclusive(date_rng, created_start, reference)
            updated = random_datetime_inclusive(
                date_rng, max(created, inventory_start), reference
            )
            created_at.append(_format_timestamp(created))
            updated_at.append(_format_timestamp(updated))
        return pd.DataFrame(
            {
                "inventory_id": ids,
                "warehouse_id": selected_warehouses,
                "product_sku": skus,
                "product_category": categories,
                "quantity_on_hand": quantities,
                "reorder_point": reorder_points,
                "last_updated_at": updated_at,
                "created_at": created_at,
            },
            columns=LOGISTICS_COLUMN_CONTRACTS["inventory"],
        )

    def _generate_and_report(self, table_name: str, generate: Any) -> Any:
        """Generate one table and report its completed shape when enabled."""

        self._report(f"Generating {table_name}")
        table = generate()
        if self.progress is not None:
            self.progress.report_table(table_name, table)
        return table

    def _report(self, message: str) -> None:
        if self.progress is not None:
            self.progress.report(message)


def _require_pandas() -> Any:
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError("pandas is required for Logistics generation") from exc
    return pd


def _choice_with_coverage(
    rng: Any,
    values: list[str],
    count: int,
    *,
    weights: dict[str, Any] | None = None,
    required: list[str] | None = None,
) -> list[str]:
    probabilities = (
        [float(weights[value]) for value in values] if weights is not None else None
    )
    selected = [str(value) for value in rng.choice(values, size=count, p=probabilities)]
    required_values = required or []
    if count < len(required_values):
        raise ValueError("Row count cannot cover required domain values")
    for position, value in enumerate(required_values):
        selected[position] = value
    rng.shuffle(selected)
    return selected


def _apply_missing_values(
    rng: Any,
    values: list[Any],
    probability: float,
    protected_positions: set[int] | None = None,
) -> None:
    protected = protected_positions or set()
    count = _count_probability(len(values), probability)
    candidates = [
        position for position in range(len(values)) if position not in protected
    ]
    if count > len(candidates):
        raise ValueError("Missing-value target exceeds eligible capacity")
    if count:
        for position in rng.choice(candidates, size=count, replace=False):
            values[int(position)] = ""


def _count_probability(total: int, probability: float) -> int:
    if not 0 <= probability <= 1:
        raise ValueError("Missing-value probability must be between zero and one")
    return round(total * probability)


def _uniform_decimal_strings(
    rng: Any, count: int, configured_range: dict[str, Any]
) -> list[str]:
    scale = int(configured_range["scale"])
    multiplier = 10**scale
    values = rng.integers(
        int(configured_range["minimum_amount"]) * multiplier,
        int(configured_range["maximum_amount"]) * multiplier + 1,
        size=count,
    )
    return [format_fixed_decimal(int(value), scale) for value in values]


def _uniform_unit_decimal_strings(
    rng: Any, count: int, configured_range: dict[str, Any]
) -> list[str]:
    scale = int(configured_range["scale"])
    values = rng.integers(
        int(configured_range["minimum_units"]),
        int(configured_range["maximum_units"]) + 1,
        size=count,
    )
    return [format_fixed_decimal(int(value), scale) for value in values]


def _uniform_integers(
    rng: Any, count: int, configured_range: dict[str, Any]
) -> list[int | str]:
    return [
        int(value)
        for value in rng.integers(
            int(configured_range["minimum"]),
            int(configured_range["maximum"]) + 1,
            size=count,
        )
    ]


def _shipment_status_for_order(
    rng: Any,
    order_status: str,
    first_for_order: bool,
    configured_weights: dict[str, Any],
) -> str:
    if order_status in {"Delivered", "Returned"} and first_for_order:
        return "Delivered"
    options = {
        "Pending": ["Booked"],
        "Shipped": ["Booked", "InTransit", "Failed", "Lost"],
        "Delivered": ["Delivered", "InTransit"],
        "Cancelled": ["Booked", "Failed"],
        "Returned": ["Delivered"],
    }
    allowed = options[order_status]
    weights = [float(configured_weights[value]) for value in allowed]
    total = sum(weights)
    probabilities = [weight / total for weight in weights]
    return str(rng.choice(allowed, p=probabilities))


def _shipment_dates(
    rng: Any,
    order_date: date,
    status: str,
    reference_today: date,
    rules: dict[str, Any],
) -> tuple[str, str]:
    if status == "Booked":
        return "", ""
    ship_range = rules["ship_lag_days"]
    ship_date = order_date + timedelta(
        days=int(
            rng.integers(
                int(ship_range["minimum"]),
                int(ship_range["maximum"]) + 1,
            )
        )
    )
    ship_date = min(ship_date, reference_today)
    if status != "Delivered":
        return ship_date.isoformat(), ""
    delivery_range = rules["delivery_lag_days"]
    delivery_date = ship_date + timedelta(
        days=int(
            rng.integers(
                int(delivery_range["minimum"]),
                int(delivery_range["maximum"]) + 1,
            )
        )
    )
    return ship_date.isoformat(), min(delivery_date, reference_today).isoformat()


def _synthetic_customer_name(fake: Any, order_id: int) -> str:
    name = re.sub(r"[^A-Za-z0-9 ]+", " ", fake.company())
    name = re.sub(r"\s+", " ", name).strip()
    return f"{name} Customer {order_id:06d}"


def _configured_datetime(value: str) -> datetime:
    return datetime.combine(date.fromisoformat(value), time.min)


def _reference_datetime(reference_today: date) -> datetime:
    return datetime.combine(reference_today, time(23, 59, 59))


def _format_timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S")


def _random_timestamp_strings(
    rng: Any, count: int, start: datetime, end: datetime
) -> list[str]:
    return [
        _format_timestamp(random_datetime_inclusive(rng, start, end))
        for _ in range(count)
    ]


__all__ = [
    "LOGISTICS_COLUMN_CONTRACTS",
    "LogisticsBaseEntityGenerator",
    "build_logistics_column_contracts",
]
