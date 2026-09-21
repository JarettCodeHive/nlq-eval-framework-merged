from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import duckdb
import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.core.manifest import compute_sha256
from generators.core.schema_contract import compact_sql
from generators.core.schema_contract import ddl_constraints
from generators.core.schema_contract import ddl_table_specs
from generators.finance.schema_sql import FinanceSchemaSQLGenerator


EXPECTED_TABLE_ORDER = [
    "accounts",
    "transactions",
    "ledger_entries",
    "budgets",
    "fx_rates",
]


def test_finance_schema_sql_matches_canonical_ddl_contract() -> None:
    generator = FinanceSchemaSQLGenerator.for_profile("full")
    sql = generator.generate_sql()
    specifications = ddl_table_specs(generator.settings.schema_source)

    assert sql == generator.settings.schema_source.read_text(encoding="utf-8")
    assert sql.encode("utf-8") == generator.settings.schema_source.read_bytes()
    assert list(specifications) == EXPECTED_TABLE_ORDER
    assert specifications["transactions"]["total_amount"] == {
        "type": "DECIMAL(19,4)",
        "nullable": False,
        "default": None,
    }
    assert specifications["fx_rates"]["rate"] == {
        "type": "DECIMAL(19,6)",
        "nullable": True,
        "default": None,
    }
    assert specifications["transactions"]["target_currency"]["default"] == "USD"
    assert specifications["transactions"]["reversed"]["default"] == "FALSE"
    assert specifications["fx_rates"]["is_estimated"]["default"] == "FALSE"


def test_finance_schema_sql_preserves_keys_and_checks() -> None:
    generator = FinanceSchemaSQLGenerator.for_profile("full")
    constraints = ddl_constraints(generator.settings.schema_source)

    assert constraints["primary_keys"] == {
        "accounts": ("account_id",),
        "transactions": ("transaction_id",),
        "ledger_entries": ("entry_id",),
        "budgets": ("budget_id",),
        "fx_rates": ("rate_id",),
    }
    assert constraints["foreign_keys"] == {
        ("accounts", "parent_account_id", "accounts", "account_id"),
        ("ledger_entries", "transaction_id", "transactions", "transaction_id"),
        ("ledger_entries", "account_id", "accounts", "account_id"),
        ("budgets", "account_id", "accounts", "account_id"),
    }
    assert constraints["unique_constraints"] == {
        ("accounts", ("account_number",)),
        ("ledger_entries", ("transaction_id", "line_number")),
        ("fx_rates", ("from_currency", "to_currency", "rate_date")),
    }
    checks = constraints["checks"]
    assert compact_sql("CHECK (total_amount > 0)") in checks["transactions"]
    assert compact_sql(
        "CHECK ((debit_amount IS NULL) != (credit_amount IS NULL))"
    ) in checks["ledger_entries"]
    assert compact_sql("CHECK (rate IS NULL OR rate > 0)") in checks["fx_rates"]


def test_finance_schema_sql_executes_in_duckdb() -> None:
    sql = FinanceSchemaSQLGenerator.for_profile("full").generate_sql()

    with duckdb.connect(database=":memory:") as connection:
        connection.execute(sql)
        tables = [
            row[0]
            for row in connection.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'main'
                ORDER BY table_name
                """
            ).fetchall()
        ]

    assert tables == sorted(EXPECTED_TABLE_ORDER)


def test_finance_schema_sql_refuses_non_release_profile() -> None:
    generator = FinanceSchemaSQLGenerator.for_profile("dev")

    with pytest.raises(ValueError, match="requires the full profile"):
        generator.write_release_schema()


def test_finance_schema_sql_writes_byte_identical_artifact_atomically(
    tmp_path: Path,
) -> None:
    generator = FinanceSchemaSQLGenerator.for_profile("full")
    generator.settings = replace(generator.settings, output_path=tmp_path)

    output_path = generator.write_release_schema()

    assert output_path == tmp_path / "schema.sql"
    assert output_path.read_bytes() == generator.settings.schema_source.read_bytes()
    assert (
        compute_sha256(output_path).sha256
        == compute_sha256(generator.settings.schema_source).sha256
    )
    assert not (tmp_path / "schema.sql.tmp").exists()


def test_finance_schema_sql_refuses_sealed_release(tmp_path: Path) -> None:
    generator = FinanceSchemaSQLGenerator.for_profile("full")
    generator.settings = replace(generator.settings, output_path=tmp_path)
    (tmp_path / "manifest.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="immutable release"):
        generator.write_release_schema()


def test_finance_schema_sql_rejects_non_finance_settings() -> None:
    settings = GenerationSettings.from_config_files("sales", "full")

    with pytest.raises(ValueError, match="only supports finance"):
        FinanceSchemaSQLGenerator(DeterministicGenerator(settings))
