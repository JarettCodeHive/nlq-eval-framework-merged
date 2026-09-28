from __future__ import annotations

from typing import Any

import pytest

from generators.logistics.imperfections import LogisticsImperfectionInjector
from generators.logistics.validators.relational import LogisticsRelationalValidator


@pytest.fixture(scope="module")
def final_tables() -> dict[str, Any]:
    return LogisticsImperfectionInjector.for_profile(
        "dev"
    ).generate_imperfect_tables()


@pytest.fixture(scope="module")
def validator() -> LogisticsRelationalValidator:
    return LogisticsRelationalValidator.for_profile("dev")


def test_final_logistics_tables_pass_full_relational_validation(
    validator: LogisticsRelationalValidator,
    final_tables: dict[str, Any],
) -> None:
    results = validator.validate_or_raise(final_tables)
    names = {result.check_name for result in results}

    assert all(result.passed for result in results)
    assert "orders.warehouse_id.declared_orphans" in names
    assert "shipments.chronology" in names
    assert "shipments.controlled_duplicates" in names
    assert "inventory.logical_pair_unique" in names
    assert "shipments.order_many_carriers" in names


def test_relational_validator_rejects_undeclared_warehouse_orphan(
    validator: LogisticsRelationalValidator,
    final_tables: dict[str, Any],
) -> None:
    changed = _copy_tables(final_tables)
    warehouse_ids = set(changed["warehouses"]["warehouse_id"])
    position = next(
        index
        for index, value in enumerate(changed["orders"]["warehouse_id"])
        if value in warehouse_ids
    )
    changed["orders"].at[position, "warehouse_id"] = 800000000
    failures = _failures(validator.validate_tables(changed))

    assert "orders.warehouse_id.unexpected_orphans" in failures
    assert "orders.warehouse_id.orphan_formula" in failures


def test_relational_validator_rejects_shipment_chronology_violation(
    validator: LogisticsRelationalValidator,
    final_tables: dict[str, Any],
) -> None:
    changed = _copy_tables(final_tables)
    position = int(changed["shipments"].index[changed["shipments"]["ship_date"] != ""][0])
    changed["shipments"].at[position, "ship_date"] = "1800-01-01"
    failures = _failures(validator.validate_tables(changed))

    assert "shipments.chronology" in failures


def test_relational_validator_rejects_missing_order_evidence(
    validator: LogisticsRelationalValidator,
    final_tables: dict[str, Any],
) -> None:
    changed = _copy_tables(final_tables)
    shipped_order_ids = set(changed["shipments"]["order_id"])
    position = next(
        index
        for index, order_id in enumerate(changed["orders"]["order_id"])
        if order_id not in shipped_order_ids
    )
    changed["orders"].at[position, "status"] = "Delivered"
    failures = _failures(validator.validate_tables(changed))

    assert "orders.shipment_evidence" in failures


def test_relational_validator_rejects_byte_identical_duplicate(
    validator: LogisticsRelationalValidator,
    final_tables: dict[str, Any],
) -> None:
    changed = _copy_tables(final_tables)
    base_count = validator.generator.row_count("shipments")
    duplicate_position = base_count
    tracking = changed["shipments"].at[duplicate_position, "tracking_number"]
    source = changed["shipments"].iloc[:base_count].loc[
        changed["shipments"].iloc[:base_count]["tracking_number"] == tracking
    ].iloc[0]
    changed["shipments"].at[duplicate_position, "shipping_cost"] = source[
        "shipping_cost"
    ]
    failures = _failures(validator.validate_tables(changed))

    assert "shipments.duplicate_semantics" in failures


def _copy_tables(tables: dict[str, Any]) -> dict[str, Any]:
    return {name: table.copy(deep=True) for name, table in tables.items()}


def _failures(results: list[Any]) -> set[str]:
    return {result.check_name for result in results if not result.passed}
