from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import duckdb
import pytest

from generators.core.schema_contract import ddl_constraints
from generators.core.schema_contract import ddl_table_specs
from generators.logistics.schema_sql import LogisticsSchemaSQLGenerator


EXPECTED_TABLE_ORDER = [
    "carriers",
    "warehouses",
    "orders",
    "shipments",
    "inventory",
]


def test_schema_sql_matches_canonical_contract_and_executes() -> None:
    generator = LogisticsSchemaSQLGenerator.for_profile("full")
    sql = generator.generate_sql()
    specifications = ddl_table_specs(generator.settings.schema_source)

    assert sql.encode("utf-8") == generator.settings.schema_source.read_bytes()
    assert list(specifications) == EXPECTED_TABLE_ORDER
    assert specifications["orders"]["total_amount"]["type"] == "DECIMAL(15,2)"
    assert specifications["orders"]["warehouse_id"]["nullable"]
    with duckdb.connect(":memory:") as connection:
        connection.execute(sql)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main'"
            ).fetchall()
        }
    assert tables == set(EXPECTED_TABLE_ORDER)


def test_schema_preserves_physical_fks_and_orphan_exception() -> None:
    generator = LogisticsSchemaSQLGenerator.for_profile("full")
    constraints = ddl_constraints(generator.settings.schema_source)

    assert constraints["foreign_keys"] == {
        ("shipments", "order_id", "orders", "order_id"),
        ("shipments", "carrier_id", "carriers", "carrier_id"),
        ("inventory", "warehouse_id", "warehouses", "warehouse_id"),
    }
    assert (
        "orders",
        "warehouse_id",
        "warehouses",
        "warehouse_id",
    ) not in constraints["foreign_keys"]


def test_schema_write_guards_and_byte_identity(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires the full profile"):
        LogisticsSchemaSQLGenerator.for_profile("dev").write_release_schema()

    generator = LogisticsSchemaSQLGenerator.for_profile("full")
    generator.settings = replace(generator.settings, output_path=tmp_path)
    output = generator.write_release_schema()

    assert output.read_bytes() == generator.settings.schema_source.read_bytes()
    assert not (tmp_path / "schema.sql.tmp").exists()
    (tmp_path / "manifest.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(FileExistsError, match="immutable release"):
        generator.write_release_schema()
