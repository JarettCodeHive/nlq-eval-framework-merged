from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import re

import duckdb
import pytest

from generators.core.schema_contract import ddl_table_specs


SCHEMA_DIR = Path("schemas/project_management")
DDL = SCHEMA_DIR / "project_management_ddl.sql"
ERD = SCHEMA_DIR / "project_management_er.dbml"
HEADER_SPEC = SCHEMA_DIR / "project_management_csv_header_spec.md"
SEMANTICS = SCHEMA_DIR / "project_management_semantics.md"
BASE_CONFIG = Path("config/generation/base.json")
DECIMAL_FIELDS = {
    ("projects", "budget_amount"): "DECIMAL(15,2)",
    ("resources", "hourly_rate"): "DECIMAL(10,2)",
    ("tasks", "estimate_hours"): "DECIMAL(8,2)",
    ("task_resources", "allocation_pct"): "DECIMAL(5,2)",
    ("time_entries", "hours"): "DECIMAL(6,2)",
}


def test_fixed_reference_date_and_quarter_are_frozen() -> None:
    base = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
    reference_today = date.fromisoformat(base["reference_today"])
    quarter_start_month = ((reference_today.month - 1) // 3) * 3 + 1
    quarter_start = date(reference_today.year, quarter_start_month, 1)
    next_quarter = (
        date(reference_today.year + 1, 1, 1)
        if quarter_start_month == 10
        else date(reference_today.year, quarter_start_month + 3, 1)
    )
    semantics = SEMANTICS.read_text(encoding="utf-8")

    assert base["reference_today"] == "2026-08-01"
    assert f"Contract v1.0 value: `{reference_today.isoformat()}`" in semantics
    assert f"entry_date >= DATE '{quarter_start.isoformat()}'" in semantics
    assert f"entry_date < DATE '{next_quarter.isoformat()}'" in semantics
    assert "must not read the wall clock" in semantics


def test_date_definitions_are_unambiguous() -> None:
    semantics = SEMANTICS.read_text(encoding="utf-8")

    required_fragments = [
        "due_date < reference_today AND completed_date IS NULL",
        "completed_date > due_date",
        "status = 'InProgress'",
        "assigned_at <= reference_today",
        "released_at IS NULL OR released_at >= reference_today",
        "The assignment is active on its `released_at` date",
        "NULL end dates remain empty in CSV",
    ]
    for fragment in required_fragments:
        assert fragment in semantics


def test_decimal_precision_and_rounding_contract_is_frozen() -> None:
    ddl = ddl_table_specs(DDL)
    semantics = SEMANTICS.read_text(encoding="utf-8")
    header = HEADER_SPEC.read_text(encoding="utf-8")
    erd = ERD.read_text(encoding="utf-8")

    for (table_name, field_name), sql_type in DECIMAL_FIELDS.items():
        assert ddl[table_name][field_name]["type"] == sql_type
        assert f"`{table_name}.{field_name}`" in semantics
    assert semantics.count("ROUND_HALF_UP") >= 2
    assert "sum exactly to `100.00`" in semantics
    assert "Binary floating-point values must not be used" in semantics
    assert "ROUND_HALF_UP" in header
    assert "ROUND_HALF_UP" in erd


def test_milestone_completion_contract_is_defensive() -> None:
    semantics = SEMANTICS.read_text(encoding="utf-8")
    header = HEADER_SPEC.read_text(encoding="utf-8")

    assert "actual_date IS NOT NULL" in semantics
    assert "NULLIF(COUNT(*), 0)" in semantics
    assert "AS DECIMAL(5, 2)" in semantics
    assert "zero denominator returns NULL" in semantics
    assert "every project at least one milestone" in semantics
    assert "Every generated project must have at least one milestone" in header


def test_currency_contract_is_strictly_usd_only() -> None:
    ddl = DDL.read_text(encoding="utf-8")
    dbml = ERD.read_text(encoding="utf-8")
    header = HEADER_SPEC.read_text(encoding="utf-8")
    semantics = SEMANTICS.read_text(encoding="utf-8")

    assert len(re.findall(r"CHECK \(currency_code = 'USD'\)", ddl)) == 2
    assert len(re.findall(r"note: 'Fixed value: USD'", dbml)) == 2
    assert "Project Management Contract v1.0 is USD-only" in semantics
    assert "Project Management has no FX conversion path" in header

    with duckdb.connect(database=":memory:") as connection:
        connection.execute(ddl)
        with pytest.raises(duckdb.ConstraintException):
            connection.execute(
                """
                INSERT INTO projects
                    (project_id, project_name, project_code, status, priority,
                     start_date, currency_code, created_at)
                VALUES
                    (1, 'Synthetic Project', 'PM-0001', 'Planning', 'Medium',
                     DATE '2026-01-01', 'EUR', TIMESTAMP '2026-01-01 00:00:00')
                """
            )
        with pytest.raises(duckdb.ConstraintException):
            connection.execute(
                """
                INSERT INTO resources
                    (resource_id, resource_name, role, currency_code, created_at)
                VALUES
                    (1, 'Synthetic Resource', 'Developer', 'EUR',
                     TIMESTAMP '2026-01-01 00:00:00')
                """
            )


def test_semantic_contract_is_referenced_by_all_schema_artifacts() -> None:
    semantic_path = "schemas/project_management/project_management_semantics.md"

    assert semantic_path in DDL.read_text(encoding="utf-8")
    assert semantic_path in ERD.read_text(encoding="utf-8")
    assert semantic_path in HEADER_SPEC.read_text(encoding="utf-8")
