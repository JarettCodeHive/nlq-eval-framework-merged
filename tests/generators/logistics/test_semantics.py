from __future__ import annotations

from pathlib import Path

import duckdb

from generators.core.schema_contract import compact_sql
from generators.core.schema_contract import ddl_constraints


ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DIR = ROOT / "schemas" / "logistics"
DDL_PATH = SCHEMA_DIR / "logistics_ddl.sql"
SEMANTICS_PATH = SCHEMA_DIR / "logistics_semantics.md"


def test_semantic_contract_freezes_required_logistics_decisions() -> None:
    semantics = SEMANTICS_PATH.read_text(encoding="utf-8")

    required_contract_values = (
        "2026-08-01",
        "2.5%",
        "1.0%",
        "900000000",
        "ROUND_HALF_UP",
        "USD-only",
        "(warehouse_id, product_sku)",
        "COUNT(DISTINCT tracking_number)",
    )
    assert all(value in semantics for value in required_contract_values)


def test_ddl_enforces_decimal_quantity_and_currency_invariants() -> None:
    checks = ddl_constraints(DDL_PATH)["checks"]
    all_checks = {
        compact_sql(check)
        for table_checks in checks.values()
        for check in table_checks
    }

    expected_fragments = (
        "currency_code='usd'",
        "base_rateisnullorbase_rate>=0",
        "utilization_pctbetween0and100",
        "total_amount>0",
        "shipping_costisnullorshipping_cost>=0",
        "quantity_on_hand>=0",
        "reorder_pointisnullorreorder_point>=0",
    )
    assert all(
        any(fragment in check for check in all_checks)
        for fragment in expected_fragments
    )


def test_duckdb_rejects_non_usd_logistics_rows() -> None:
    with duckdb.connect(database=":memory:") as connection:
        connection.execute(DDL_PATH.read_text(encoding="utf-8"))

        for statement in (
            "INSERT INTO carriers VALUES "
            "(1, 'Carrier', 'Standard', NULL, NULL, 'EUR', TRUE, "
            "TIMESTAMP '2026-01-01 00:00:00')",
            "INSERT INTO orders VALUES "
            "(1, NULL, 'Customer', DATE '2026-01-01', 'Pending', "
            "'Medium', 10.00, 'INR', TIMESTAMP '2026-01-01 00:00:00')",
        ):
            try:
                connection.execute(statement)
            except duckdb.ConstraintException:
                continue
            raise AssertionError("DuckDB accepted a non-USD Logistics row")
