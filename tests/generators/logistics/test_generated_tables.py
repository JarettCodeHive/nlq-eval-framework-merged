from __future__ import annotations

from typing import Any

import pytest

from generators.logistics.generator import LogisticsBaseEntityGenerator
from generators.logistics.validators.generated_tables import (
    validate_logistics_generated_tables,
)


@pytest.fixture(scope="module")
def generated() -> tuple[Any, dict[str, Any]]:
    entity_generator = LogisticsBaseEntityGenerator.for_profile("dev")
    return entity_generator.generator, entity_generator.generate_tables()


def _copy(tables: dict[str, Any]) -> dict[str, Any]:
    return {name: table.copy(deep=True) for name, table in tables.items()}


def test_generated_tables_guard_accepts_clean_base_tables(
    generated: tuple[Any, dict[str, Any]],
) -> None:
    generator, tables = generated
    validate_logistics_generated_tables(generator, tables)


def test_generated_tables_guard_rejects_contract_drift(
    generated: tuple[Any, dict[str, Any]],
) -> None:
    generator, clean = generated
    tables = _copy(clean)
    tables["orders"] = tables["orders"].iloc[:-1].copy()

    with pytest.raises(ValueError, match="row count differs"):
        validate_logistics_generated_tables(generator, tables)


def test_generated_tables_guard_rejects_clean_warehouse_orphans(
    generated: tuple[Any, dict[str, Any]],
) -> None:
    generator, clean = generated
    tables = _copy(clean)
    tables["orders"].loc[0, "warehouse_id"] = 900000001

    with pytest.raises(ValueError, match="warehouse orphans"):
        validate_logistics_generated_tables(generator, tables)


def test_generated_tables_guard_rejects_duplicate_shipment_business_keys(
    generated: tuple[Any, dict[str, Any]],
) -> None:
    generator, clean = generated
    tables = _copy(clean)
    fields = ["order_id", "carrier_id", "tracking_number"]
    tables["shipments"].loc[1, fields] = tables["shipments"].loc[0, fields].values

    with pytest.raises(ValueError, match="duplicate business keys"):
        validate_logistics_generated_tables(generator, tables)


def test_generated_tables_guard_rejects_status_date_drift(
    generated: tuple[Any, dict[str, Any]],
) -> None:
    generator, clean = generated
    tables = _copy(clean)
    position = tables["shipments"].index[
        tables["shipments"]["status"] == "Delivered"
    ][0]
    tables["shipments"].loc[position, "delivery_date"] = ""

    with pytest.raises(ValueError, match="invalid delivery_date"):
        validate_logistics_generated_tables(generator, tables)


def test_generated_tables_guard_rejects_currency_and_scale_drift(
    generated: tuple[Any, dict[str, Any]],
) -> None:
    generator, clean = generated
    tables = _copy(clean)
    tables["orders"].loc[0, "total_amount"] = "10.1"

    with pytest.raises(ValueError, match="fixed scale"):
        validate_logistics_generated_tables(generator, tables)

    tables = _copy(clean)
    tables["orders"].loc[0, "currency_code"] = "EUR"
    with pytest.raises(ValueError, match="non-USD"):
        validate_logistics_generated_tables(generator, tables)


def test_generated_tables_guard_rejects_inventory_grain_drift(
    generated: tuple[Any, dict[str, Any]],
) -> None:
    generator, clean = generated
    tables = _copy(clean)
    fields = ["warehouse_id", "product_sku"]
    tables["inventory"].loc[1, fields] = tables["inventory"].loc[0, fields].values

    with pytest.raises(ValueError, match="duplicate warehouse/SKU"):
        validate_logistics_generated_tables(generator, tables)
