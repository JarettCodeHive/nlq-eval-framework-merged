"""Controlled imperfection injection for distributed Logistics data."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.imperfections import count_from_pct
from generators.core.imperfections import inject_fixed_scale_outliers
from generators.core.imperfections import select_disjoint_positions
from generators.core.progress import ProgressReporter
from generators.logistics.config import load_logistics_config
from generators.logistics.config import settings_for_profile
from generators.logistics.distributions import LogisticsDistributionApplier
from generators.logistics.validators.config import validate_logistics_config
from generators.logistics.validators.distributed_tables import (
    validate_logistics_distributed_tables,
)
from generators.logistics.validators.imperfect_tables import (
    validate_logistics_imperfect_tables,
)


class LogisticsImperfectionInjector:
    """Inject approved Logistics defects without breaking physical integrity.

    Starting from validated distributed tables, the injector appends logical
    shipment duplicates with fresh IDs, introduces disjoint missing and orphan
    warehouse assignments, replaces selected order totals with fixed-scale
    outliers, and moves complete order/shipment paths onto configured boundary
    dates. Shipment status/date rules, physical foreign keys, and USD values
    remain valid throughout the stage.
    """

    def __init__(
        self,
        generator: DeterministicGenerator,
        progress: ProgressReporter | None = None,
    ) -> None:
        if generator.settings.domain != "logistics":
            raise ValueError("LogisticsImperfectionInjector only supports logistics")
        validate_logistics_config()
        self.generator = generator
        self.settings = generator.settings
        self.config = self.settings.imperfections
        self.logistics_config = load_logistics_config()
        self.targets = self.logistics_config["imperfection_targets"]
        self.progress = progress

    @classmethod
    def for_profile(
        cls,
        profile: str,
        progress: ProgressReporter | None = None,
    ) -> "LogisticsImperfectionInjector":
        """Create an injector from validated Logistics profile configuration."""

        return cls(
            DeterministicGenerator(settings_for_profile(profile)),
            progress=progress,
        )

    def generate_imperfect_tables(self) -> dict[str, Any]:
        """Generate fresh distributed Logistics tables and inject defects."""

        distributed = LogisticsDistributionApplier(
            self.generator,
            progress=self.progress,
        ).generate_distributed_tables()
        return self.apply_to_tables(distributed)

    def apply_to_tables(self, tables: dict[str, Any]) -> dict[str, Any]:
        """Return imperfect deep copies of valid distributed Logistics tables."""

        self._report("Injecting controlled Logistics imperfections")
        validate_logistics_distributed_tables(self.generator, tables)
        imperfect = {
            table_name: table.copy(deep=True) for table_name, table in tables.items()
        }

        imperfect["shipments"], duplicate_order_ids = (
            self._inject_near_duplicate_shipments(imperfect["shipments"])
        )
        self._report("Injected near-duplicate shipment records")

        self._inject_missing_and_orphan_warehouses(imperfect["orders"])
        self._report("Injected disjoint missing and orphan warehouse assignments")

        outlier = self.targets["order_total_outliers"]
        imperfect[outlier["table"]] = inject_fixed_scale_outliers(
            imperfect[outlier["table"]],
            outlier["field"],
            self.generator.rng_for(
                "imperfections:logistics:orders:total_amount_outliers"
            ),
            float(self.config["outlier_pct"]),
            min_outlier=int(outlier["minimum_value"]),
            max_outlier=int(outlier["maximum_value"]),
            scale=int(outlier["scale"]),
        )
        self._report("Injected order-total outliers")

        self._inject_coordinated_boundary_dates(imperfect, duplicate_order_ids)
        self._report("Injected coordinated Logistics boundary dates")
        self._report("Validating imperfect Logistics tables")
        validate_logistics_imperfect_tables(
            self.generator,
            imperfect,
            source_tables=tables,
        )
        self._report("Logistics imperfection injection complete")
        return imperfect

    def _inject_near_duplicate_shipments(
        self,
        shipments: Any,
    ) -> tuple[Any, set[int]]:
        """Append logical shipment duplicates with fresh sequential IDs."""

        duplicate_count = count_from_pct(
            len(shipments), float(self.config["duplicate_pct"])
        )
        result = shipments.copy(deep=True)
        if duplicate_count == 0:
            return result, set()

        rng = self.generator.rng_for(
            "imperfections:logistics:shipments:near_duplicates"
        )
        positions = rng.choice(
            shipments.index.to_numpy(),
            size=duplicate_count,
            replace=False,
        )
        duplicates = shipments.loc[positions].copy(deep=True).reset_index(drop=True)
        source_order_ids = {int(value) for value in duplicates["order_id"]}
        start_id = int(shipments["shipment_id"].max()) + 1
        duplicates["shipment_id"] = range(start_id, start_id + duplicate_count)

        cost_rules = self.logistics_config["generation_rules"]["shipments"][
            "shipping_cost"
        ]
        duplicates["shipping_cost"] = [
            _near_duplicate_shipping_cost(
                value,
                minimum=Decimal(str(cost_rules["minimum_amount"])),
                maximum=Decimal(str(cost_rules["maximum_amount"])),
                scale=int(cost_rules["scale"]),
            )
            for value in duplicates["shipping_cost"]
        ]

        pd = _require_pandas()
        return pd.concat([result, duplicates], ignore_index=True), source_order_ids

    def _inject_missing_and_orphan_warehouses(self, orders: Any) -> None:
        """Inject exact, deterministic, disjoint warehouse defect groups."""

        missing = self.targets["missing_order_warehouses"]
        orphan = self.targets["orphaned_order_warehouses"]
        groups = select_disjoint_positions(
            self.generator.rng_for(
                "imperfections:logistics:orders:warehouse_assignments"
            ),
            len(orders),
            {
                missing["selection_group"]: float(self.config["null_pct"]),
                orphan["selection_group"]: float(orphan["rate_pct"]),
            },
        )
        orders[missing["field"]] = orders[missing["field"]].astype("object")
        null_positions = list(groups[missing["selection_group"]])
        orphan_positions = list(groups[orphan["selection_group"]])
        orders.loc[null_positions, missing["field"]] = ""
        orders.loc[orphan_positions, orphan["field"]] = [
            int(orphan["namespace_base"]) + int(orders.loc[position, "order_id"])
            for position in orphan_positions
        ]

    def _inject_coordinated_boundary_dates(
        self,
        tables: dict[str, Any],
        excluded_order_ids: set[int],
    ) -> None:
        """Place boundaries on coherent order and delivered-shipment paths."""

        boundaries = [
            date.fromisoformat(value) for value in self.config["boundary_dates"]
        ]
        if not boundaries:
            return

        shipments = tables["shipments"]
        delivered_order_ids = sorted(
            {
                int(value)
                for value in shipments.loc[
                    shipments["status"] == "Delivered", "order_id"
                ]
                if int(value) not in excluded_order_ids
            }
        )
        if len(delivered_order_ids) < len(boundaries):
            raise ValueError(
                "Logistics boundary injection lacks eligible delivered order paths"
            )

        orders = tables["orders"]
        for boundary, order_id in zip(
            boundaries,
            delivered_order_ids[: len(boundaries)],
            strict=True,
        ):
            boundary_date = boundary.isoformat()
            boundary_timestamp = f"{boundary_date}T00:00:00"
            order_position = int(orders.index[orders["order_id"] == order_id][0])
            orders.at[order_position, "order_date"] = boundary_date
            orders.at[order_position, "created_at"] = boundary_timestamp

            positions = shipments.index[shipments["order_id"] == order_id]
            for position in positions:
                status = str(shipments.at[position, "status"])
                shipments.at[position, "created_at"] = boundary_timestamp
                if status == "Booked":
                    shipments.at[position, "ship_date"] = ""
                    shipments.at[position, "delivery_date"] = ""
                elif status == "Delivered":
                    shipments.at[position, "ship_date"] = boundary_date
                    shipments.at[position, "delivery_date"] = boundary_date
                else:
                    shipments.at[position, "ship_date"] = boundary_date
                    shipments.at[position, "delivery_date"] = ""

    def _report(self, message: str) -> None:
        if self.progress is not None:
            self.progress.report(message)


def _near_duplicate_shipping_cost(
    value: Any,
    minimum: Decimal,
    maximum: Decimal,
    scale: int,
) -> str:
    """Change shipping cost by one minor unit while retaining valid scale."""

    if value == "" or value is None:
        return f"{minimum:.{scale}f}"
    current = Decimal(str(value))
    unit = Decimal(1).scaleb(-scale)
    changed = current - unit if current >= maximum else current + unit
    changed = min(max(changed, minimum), maximum)
    return f"{changed:.{scale}f}"


def _require_pandas() -> Any:
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError("pandas is required for Logistics imperfections") from exc
    return pd


__all__ = ["LogisticsImperfectionInjector"]
