"""Human-readable Logistics summaries for the root CLI."""

from __future__ import annotations

from typing import Any

from generators.logistics.imperfections import LogisticsImperfectionInjector


def print_relationship_summary(tables: dict[str, Any]) -> None:
    """Print concise Logistics relationship and cardinality coverage."""

    carrier_ids = set(tables["carriers"]["carrier_id"])
    warehouse_ids = set(tables["warehouses"]["warehouse_id"])
    order_ids = set(tables["orders"]["order_id"])
    shipments = tables["shipments"]
    inventory = tables["inventory"]

    print("relationship_checks:")
    print(
        "  shipments.order_id valid: "
        f"{set(shipments['order_id']).issubset(order_ids)}"
    )
    print(
        "  shipments.carrier_id valid: "
        f"{set(shipments['carrier_id']).issubset(carrier_ids)}"
    )
    print(
        "  inventory.warehouse_id valid: "
        f"{set(inventory['warehouse_id']).issubset(warehouse_ids)}"
    )
    print(
        "  orders_with_multiple_carriers: "
        f"{int(shipments.groupby('order_id')['carrier_id'].nunique().ge(2).sum())}"
    )
    print(
        "  carriers_with_multiple_orders: "
        f"{int(shipments.groupby('carrier_id')['order_id'].nunique().ge(2).sum())}"
    )


def print_distribution_summary(tables: dict[str, Any]) -> None:
    """Print concise Logistics amount, frequency, and date measurements."""

    orders = tables["orders"]
    shipments = tables["shipments"]
    totals = orders["total_amount"].astype(float)
    shipment_counts = shipments.groupby("order_id").size()

    print("distribution_checks:")
    print(f"  order_total_min: {totals.min():.2f}")
    print(f"  order_total_max: {totals.max():.2f}")
    print(f"  order_total_mean: {totals.mean():.2f}")
    print(f"  shipments_per_shipped_order_mean: {shipment_counts.mean():.2f}")
    print(f"  shipments_per_shipped_order_max: {shipment_counts.max()}")
    print(f"  distinct_order_dates: {orders['order_date'].nunique()}")


def print_imperfection_summary(
    tables: dict[str, Any],
    injector: LogisticsImperfectionInjector,
) -> None:
    """Print concise Logistics imperfection measurements."""

    orders = tables["orders"]
    shipments = tables["shipments"]
    warehouses = tables["warehouses"]
    warehouse_ids = set(warehouses["warehouse_id"])
    duplicate_rows = len(shipments) - injector.generator.row_count("shipments")
    orphan_count = int(
        orders["warehouse_id"].map(
            lambda value: value != "" and value not in warehouse_ids
        ).sum()
    )
    outlier_minimum = float(
        injector.logistics_config["imperfection_targets"][
            "order_total_outliers"
        ]["minimum_value"]
    )
    boundaries = set(injector.config["boundary_dates"])

    print("imperfection_checks:")
    print(f"  shipment_near_duplicates: {duplicate_rows}")
    print(f"  orders.warehouse_id_empty: {int(orders['warehouse_id'].eq('').sum())}")
    print(f"  orders.warehouse_id_declared_orphans: {orphan_count}")
    totals = orders["total_amount"].astype(float)
    print(
        f"  order_total_outliers_ge_{int(outlier_minimum)}: "
        f"{int(totals.ge(outlier_minimum).sum())}"
    )
    print(
        "  order_boundary_dates: "
        f"{int(orders['order_date'].astype(str).isin(boundaries).sum())}"
    )


__all__ = [
    "print_distribution_summary",
    "print_imperfection_summary",
    "print_relationship_summary",
]
