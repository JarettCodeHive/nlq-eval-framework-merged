"""Typed-DuckDB access. The verifier NEVER uses read_csv_auto - it queries
the database generate_crm.py built from the DDL, so DECIMAL stays DECIMAL
and zero-tolerance exact-match holds."""

from __future__ import annotations

from pathlib import Path

import duckdb


def connect_typed(db_path: str | Path) -> duckdb.DuckDBPyConnection:
    p = Path(db_path)
    if not p.exists():
        raise SystemExit(f"missing {p} - run generator/generate_crm.py first")
    return duckdb.connect(str(p), read_only=True)


def distinct(con, sql: str) -> list:
    return [r[0] for r in con.execute(sql).fetchall() if r[0] is not None]
