"""Deterministic distribution application for clean Logistics data."""

from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.distributions import gaussian_mixture_dates
from generators.core.distributions import pareto_decimal_strings
from generators.core.distributions import poisson_weights
from generators.core.progress import ProgressReporter
from generators.logistics.config import load_logistics_config
from generators.logistics.config import settings_for_profile
from generators.logistics.generator import LogisticsBaseEntityGenerator
from generators.logistics.validators.config import validate_logistics_config
from generators.logistics.validators.distributed_tables import (
    validate_logistics_distributed_tables,
)
from generators.logistics.validators.generated_tables import (
    validate_logistics_generated_tables,
)


class LogisticsDistributionApplier:
    """Apply realistic distributions without changing Logistics contracts.

    Order totals receive bounded fixed-scale Pareto values. Shipment rows are
    redistributed over the same shipment-bearing orders using Poisson-derived
    frequencies while preserving exact row counts and valid carrier links.
    Order, shipment, and inventory business dates receive Gaussian-mixture
    clustering, after which dependent dates and timestamps are rebuilt to keep
    status rules and chronology valid.
    """

    def __init__(
        self,
        generator: DeterministicGenerator,
        progress: ProgressReporter | None = None,
    ) -> None:
        if generator.settings.domain != "logistics":
            raise ValueError("LogisticsDistributionApplier only supports logistics")
        validate_logistics_config()
        self.generator = generator
        self.settings = generator.settings
        self.config = load_logistics_config()
        self.rules = self.config["generation_rules"]
        self.progress = progress

    @classmethod
    def for_profile(
        cls,
        profile: str,
        progress: ProgressReporter | None = None,
    ) -> "LogisticsDistributionApplier":
        """Create an applier from validated Logistics profile configuration."""

        return cls(
            DeterministicGenerator(settings_for_profile(profile)),
            progress=progress,
        )

    def generate_distributed_tables(self) -> dict[str, Any]:
        """Generate fresh clean Logistics tables and apply distributions."""

        base_tables = LogisticsBaseEntityGenerator(
            self.generator,
            progress=self.progress,
        ).generate_tables()
        return self.apply_to_tables(base_tables)

    def apply_to_tables(self, tables: dict[str, Any]) -> dict[str, Any]:
        """Return distributed deep copies of valid clean Logistics tables."""

        self._report("Applying Logistics distributions")
        validate_logistics_generated_tables(self.generator, tables)
        distributed = {
            table_name: table.copy(deep=True) for table_name, table in tables.items()
        }
        self._apply_order_total_distribution(distributed["orders"])
        self._report("Applied Pareto order-total distribution")
        self._apply_shipment_frequency_distribution(distributed)
        self._report("Applied Poisson shipment-frequency distribution")
        self._apply_date_distributions(distributed)
        self._report("Applied Gaussian-mixture date distributions")
        validate_logistics_distributed_tables(
            self.generator,
            distributed,
            source_tables=tables,
        )
        self._report("Logistics distribution application complete")
        return distributed

    def _apply_order_total_distribution(self, orders: Any) -> None:
        target = self.config["distribution_targets"]["order_total"]
        spec = self.settings.distributions[target["settings_key"]]
        orders[target["field"]] = pareto_decimal_strings(
            self.generator.rng_for("distribution:logistics:orders:total_amount"),
            count=len(orders),
            alpha=float(spec["alpha"]),
            min_amount=int(spec["min_amount"]),
            max_amount=int(spec["max_amount"]),
            scale=int(spec["scale"]),
        )

    def _apply_shipment_frequency_distribution(
        self, tables: dict[str, Any]
    ) -> None:
        """Redistribute shipments while retaining eligible and unshipped orders."""

        shipments = tables["shipments"]
        eligible_order_ids = sorted({int(value) for value in shipments["order_id"]})
        carrier_ids = [int(value) for value in tables["carriers"]["carrier_id"]]
        target = self.config["distribution_targets"]["shipment_frequency"]
        spec = self.settings.distributions[target["settings_key"]]
        rng = self.generator.rng_for(
            "distribution:logistics:shipments:frequency"
        )
        counts = _exact_poisson_counts(
            rng,
            item_count=len(eligible_order_ids),
            total_count=len(shipments),
            lam=float(spec["lambda"]),
            first_item_minimum=2,
        )
        order_ids: list[int] = []
        assigned_carriers: list[int] = []
        for position, (order_id, frequency) in enumerate(
            zip(eligible_order_ids, counts, strict=True)
        ):
            order_ids.extend([order_id] * frequency)
            first_carrier = carrier_ids[position % len(carrier_ids)]
            order_carriers = [first_carrier]
            if frequency > 1:
                order_carriers.extend(
                    int(value)
                    for value in rng.choice(
                        carrier_ids,
                        size=frequency - 1,
                        replace=True,
                    )
                )
            if position == 0 and frequency > 1:
                order_carriers[1] = carrier_ids[1 % len(carrier_ids)]
            assigned_carriers.extend(order_carriers)

        shipments["order_id"] = order_ids
        shipments["carrier_id"] = assigned_carriers
        self._rebuild_shipment_statuses(tables)

    def _rebuild_shipment_statuses(self, tables: dict[str, Any]) -> None:
        shipments = tables["shipments"]
        order_status = dict(
            zip(tables["orders"]["order_id"], tables["orders"]["status"], strict=True)
        )
        weights = self.rules["shipments"]["status_weights"]
        rng = self.generator.rng_for(
            "distribution:logistics:shipments:statuses"
        )
        seen: set[int] = set()
        statuses: list[str] = []
        for order_id in shipments["order_id"]:
            order_id = int(order_id)
            first = order_id not in seen
            seen.add(order_id)
            statuses.append(
                _compatible_status(rng, str(order_status[order_id]), first, weights)
            )
        shipments["status"] = statuses

    def _apply_date_distributions(self, tables: dict[str, Any]) -> None:
        spec = self.settings.distributions["date_clustering"]
        order_start = date.fromisoformat(
            self.rules["date_windows"]["order_activity_start"]
        )
        order_end = self.settings.reference_today - timedelta(days=45)
        orders = tables["orders"]
        orders["order_date"] = _clustered_dates(
            self.generator.rng_for("distribution:logistics:orders:order_date"),
            len(orders),
            order_start,
            order_end,
            spec,
        )
        orders["created_at"] = [
            _clamp_timestamp_on_or_before(value, date.fromisoformat(order_date))
            for value, order_date in zip(
                orders["created_at"], orders["order_date"], strict=True
            )
        ]
        self._rebuild_shipment_dates(tables, spec)
        self._cluster_inventory_updates(tables["inventory"], spec)

    def _rebuild_shipment_dates(
        self, tables: dict[str, Any], spec: dict[str, Any]
    ) -> None:
        shipments = tables["shipments"]
        orders = tables["orders"]
        order_dates = {
            int(order_id): date.fromisoformat(order_date)
            for order_id, order_date in zip(
                orders["order_id"], orders["order_date"], strict=True
            )
        }
        order_created = dict(
            zip(orders["order_id"], orders["created_at"], strict=True)
        )
        start = date.fromisoformat(self.rules["date_windows"]["order_activity_start"])
        reference = self.settings.reference_today
        ship_samples = _clustered_dates(
            self.generator.rng_for("distribution:logistics:shipments:ship_date"),
            len(shipments),
            start,
            reference - timedelta(days=31),
            spec,
        )
        delivery_samples = _clustered_dates(
            self.generator.rng_for("distribution:logistics:shipments:delivery_date"),
            len(shipments),
            start,
            reference - timedelta(days=1),
            spec,
        )
        ship_lag = self.rules["shipments"]["ship_lag_days"]
        delivery_lag = self.rules["shipments"]["delivery_lag_days"]
        ship_dates: list[str] = []
        delivery_dates: list[str] = []
        created_values: list[str] = []
        for position, row in enumerate(shipments.itertuples(index=False)):
            order_date = order_dates[int(row.order_id)]
            if row.status == "Booked":
                ship_date = None
                delivery_date = None
                event_end = datetime.combine(reference - timedelta(days=1), time.max)
            else:
                minimum_ship = order_date + timedelta(days=int(ship_lag["minimum"]))
                maximum_ship = min(
                    reference - timedelta(days=31),
                    order_date + timedelta(days=int(ship_lag["maximum"])),
                )
                ship_date = _clamp_date(
                    date.fromisoformat(ship_samples[position]),
                    minimum_ship,
                    maximum_ship,
                )
                if row.status == "Delivered":
                    minimum_delivery = ship_date + timedelta(
                        days=int(delivery_lag["minimum"])
                    )
                    maximum_delivery = min(
                        reference - timedelta(days=1),
                        ship_date + timedelta(days=int(delivery_lag["maximum"])),
                    )
                    delivery_date = _clamp_date(
                        date.fromisoformat(delivery_samples[position]),
                        minimum_delivery,
                        maximum_delivery,
                    )
                else:
                    delivery_date = None
                event_end = datetime.combine(ship_date, time.max)
            creation_start = datetime.fromisoformat(order_created[int(row.order_id)])
            old_created = datetime.fromisoformat(row.created_at)
            created_values.append(
                _format_timestamp(min(event_end, max(creation_start, old_created)))
            )
            ship_dates.append(ship_date.isoformat() if ship_date else "")
            delivery_dates.append(
                delivery_date.isoformat() if delivery_date else ""
            )
        shipments["ship_date"] = ship_dates
        shipments["delivery_date"] = delivery_dates
        shipments["created_at"] = created_values

    def _cluster_inventory_updates(
        self, inventory: Any, spec: dict[str, Any]
    ) -> None:
        start = date.fromisoformat(
            self.rules["date_windows"]["inventory_activity_start"]
        )
        reference = self.settings.reference_today
        samples = _clustered_dates(
            self.generator.rng_for(
                "distribution:logistics:inventory:last_updated_at"
            ),
            len(inventory),
            start,
            reference,
            spec,
        )
        inventory["last_updated_at"] = [
            _format_timestamp(
                max(
                    datetime.fromisoformat(created_at),
                    datetime.combine(date.fromisoformat(sample), time(12, 0)),
                )
            )
            for created_at, sample in zip(
                inventory["created_at"], samples, strict=True
            )
        ]

    def _report(self, message: str) -> None:
        if self.progress is not None:
            self.progress.report(message)


def _exact_poisson_counts(
    rng: Any,
    item_count: int,
    total_count: int,
    lam: float,
    first_item_minimum: int = 1,
) -> list[int]:
    """Allocate an exact total using positive Poisson-derived frequencies."""

    minimums = [1] * item_count
    minimums[0] = first_item_minimum
    required = sum(minimums)
    if total_count < required:
        raise ValueError("Poisson total cannot satisfy minimum item coverage")
    remaining = total_count - required
    weights = poisson_weights(rng, item_count, lam)
    extras = rng.multinomial(remaining, weights)
    return [minimum + int(extra) for minimum, extra in zip(minimums, extras, strict=True)]


def _compatible_status(
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
    }[order_status]
    weights = [float(configured_weights[value]) for value in options]
    total = sum(weights)
    return str(rng.choice(options, p=[weight / total for weight in weights]))


def _clustered_dates(
    rng: Any,
    count: int,
    start: date,
    end: date,
    spec: dict[str, Any],
) -> list[str]:
    return gaussian_mixture_dates(
        rng,
        count=count,
        start=start,
        end=end,
        component_centers=tuple(float(value) for value in spec["component_centers"]),
        component_weights=tuple(float(value) for value in spec["component_weights"]),
        std_fraction=float(spec["std_fraction"]),
    )


def _clamp_date(value: date, minimum: date, maximum: date) -> date:
    if maximum < minimum:
        raise ValueError("Logistics date window is reversed")
    return min(maximum, max(minimum, value))


def _clamp_timestamp_on_or_before(value: str, upper_date: date) -> str:
    timestamp = datetime.fromisoformat(value)
    return _format_timestamp(min(timestamp, datetime.combine(upper_date, time.max)))


def _format_timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S")


__all__ = ["LogisticsDistributionApplier"]
