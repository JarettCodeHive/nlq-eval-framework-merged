from __future__ import annotations

import re
from pathlib import Path

import duckdb

from generators.core.schema_contract import ddl_table_specs
from generators.core.schema_contract import header_table_specs


SCHEMA_DIR = Path("schemas/finance")
DDL = SCHEMA_DIR / "finance_ddl.sql"
ERD = SCHEMA_DIR / "finance_er.dbml"
HEADER_SPEC = SCHEMA_DIR / "finance_csv_header_spec.md"
TABLE_ORDER = [
    "accounts",
    "transactions",
    "ledger_entries",
    "budgets",
    "fx_rates",
]
SOURCE_CURRENCIES = [
    "USD",
    "EUR",
    "GBP",
    "INR",
    "JPY",
    "CAD",
    "AUD",
    "CHF",
    "SGD",
    "AED",
]

SQL_TYPE = (
    r"INTEGER|VARCHAR(?:\(\d+\))?|CHAR\(\d+\)|"
    r"DECIMAL\(\d+,\s*\d+\)|BOOLEAN|DATE|TIMESTAMP"
)


def _normalized_default(value: str | None) -> str | None:
    """Normalize SQL and DBML defaults for direct comparison."""

    if value is None or value.startswith("'"):
        return value
    return value.upper()


def _table_bodies(text: str, start_pattern: str) -> dict[str, str]:
    matches = list(re.finditer(start_pattern, text, re.MULTILINE))
    return {
        match.group(1): (
            text[match.end() : matches[index + 1].start()]
            if index + 1 < len(matches)
            else text[match.end() :]
        )
        for index, match in enumerate(matches)
    }


def _dbml_contract() -> dict[str, list[tuple[str, str, bool, str | None]]]:
    tables = _table_bodies(
        ERD.read_text(encoding="utf-8"),
        r"^Table ([a-z_]+) \{\n",
    )
    contract: dict[str, list[tuple[str, str, bool, str | None]]] = {}
    for table_name, body in tables.items():
        fields = []
        for name, sql_type, attributes in re.findall(
            rf"^  ([a-z][a-z0-9_]*) ({SQL_TYPE})(?: \[([^\n]+)\])?$",
            body,
            re.MULTILINE | re.IGNORECASE,
        ):
            default_match = re.search(r"\bdefault:\s*('[^']+'|\w+)", attributes)
            fields.append(
                (
                    name,
                    sql_type.replace(" ", "").upper(),
                    "not null" not in attributes.lower(),
                    _normalized_default(
                        default_match.group(1) if default_match else None
                    ),
                )
            )
        contract[table_name] = fields
    return contract


def _ddl_contract() -> dict[str, list[tuple[str, str, bool, str | None]]]:
    return {
        table_name: [
            (
                field_name,
                field["type"],
                field["nullable"],
                _normalized_default(
                    f"'{field['default']}'"
                    if field["default"] == "USD"
                    else field["default"]
                ),
            )
            for field_name, field in fields.items()
        ]
        for table_name, fields in ddl_table_specs(DDL).items()
    }


def test_ddl_dbml_and_header_contracts_are_aligned() -> None:
    ddl = _ddl_contract()
    dbml = _dbml_contract()
    headers = header_table_specs(HEADER_SPEC)

    assert list(ddl) == TABLE_ORDER
    assert list(dbml) == TABLE_ORDER
    assert list(headers) == TABLE_ORDER
    for table_name in TABLE_ORDER:
        assert dbml[table_name] == ddl[table_name]
        assert [
            (field["name"], field["type"], field["nullable"])
            for field in headers[table_name]
        ] == [
            (name, sql_type, nullable)
            for name, sql_type, nullable, _ in ddl[table_name]
        ]


def test_ddl_executes_in_duckdb_with_expected_tables() -> None:
    with duckdb.connect(database=":memory:") as connection:
        connection.execute(DDL.read_text(encoding="utf-8"))
        assert [
            row[0] for row in connection.execute("SHOW TABLES").fetchall()
        ] == sorted(TABLE_ORDER)


def test_finance_precision_contract_is_explicit() -> None:
    ddl = DDL.read_text(encoding="utf-8")
    header = HEADER_SPEC.read_text(encoding="utf-8")

    assert len(re.findall(r"^    \w+ DECIMAL\(19, 4\)", ddl, re.MULTILINE)) == 4
    assert "rate DECIMAL(19, 6)" in ddl
    assert "ROUND_HALF_UP" in ddl
    assert "ROUND_HALF_UP" in header
    assert "round once" in header


def test_reporting_currency_is_strictly_usd() -> None:
    ddl = DDL.read_text(encoding="utf-8")
    dbml = ERD.read_text(encoding="utf-8")

    assert "CHECK (target_currency = 'USD')" in ddl
    assert "CHECK (currency_code = 'USD')" in ddl
    assert "CHECK (to_currency = 'USD')" in ddl
    assert "sole target/reporting currency" in dbml

    with duckdb.connect(database=":memory:") as connection:
        connection.execute(ddl)
        _insert_account(connection)
        _assert_constraint_error(
            connection,
            """
            INSERT INTO transactions VALUES
            (1, DATE '2026-01-01', NULL, 'ERP', 'EUR', 'EUR',
             10.0000, FALSE, TIMESTAMP '2026-01-01 10:00:00')
            """,
        )
        _assert_constraint_error(
            connection,
            """
            INSERT INTO budgets VALUES
            (1, 1, 2026, DATE '2026-01-01', DATE '2026-04-01',
             10.0000, 'EUR', 'Approved', TIMESTAMP '2025-12-01 00:00:00')
            """,
        )
        _assert_constraint_error(
            connection,
            """
            INSERT INTO fx_rates VALUES
            (1, 'EUR', 'GBP', DATE '2026-01-01', 0.800000,
             'Synthetic', FALSE, TIMESTAMP '2026-01-01 00:00:00')
            """,
        )


def test_supported_source_currency_contract_is_frozen() -> None:
    ddl = DDL.read_text(encoding="utf-8")
    dbml = ERD.read_text(encoding="utf-8")
    header = HEADER_SPEC.read_text(encoding="utf-8")

    for currency in SOURCE_CURRENCIES:
        assert currency in ddl
        assert currency in dbml
        assert f"`{currency}`" in header
    assert "Five percent of transactions" in ddl
    assert "5% of transactions" in dbml
    assert "5% of transactions" in header

    with duckdb.connect(database=":memory:") as connection:
        connection.execute(ddl)
        _assert_constraint_error(
            connection,
            """
            INSERT INTO accounts VALUES
            (1, '1000', 'Cash', 'Asset', 'Cash', 'XXX', NULL, 'Debit', TRUE,
             TIMESTAMP '2025-01-01 00:00:00')
            """,
        )
        _insert_account(connection)
        _assert_constraint_error(
            connection,
            """
            INSERT INTO transactions VALUES
            (1, DATE '2026-01-01', NULL, 'ERP', 'XXX', 'USD',
             10.0000, FALSE, TIMESTAMP '2026-01-01 10:00:00')
            """,
        )
        _assert_constraint_error(
            connection,
            """
            INSERT INTO fx_rates VALUES
            (1, 'XXX', 'USD', DATE '2026-01-01', 1.000000,
             'Synthetic', FALSE, TIMESTAMP '2026-01-01 00:00:00')
            """,
        )


def test_unique_business_and_analytical_keys_are_enforced() -> None:
    with duckdb.connect(database=":memory:") as connection:
        connection.execute(DDL.read_text(encoding="utf-8"))
        _insert_account(connection)
        _assert_constraint_error(
            connection,
            """
            INSERT INTO accounts VALUES
            (2, '1000', 'Duplicate Number', 'Asset', 'Cash', 'USD',
             NULL, 'Debit', TRUE, TIMESTAMP '2025-01-01 00:00:00')
            """,
        )
        _insert_transaction(connection)
        connection.execute(
            """
            INSERT INTO ledger_entries VALUES
            (1, 1, 1, 1, 10.0000, NULL, 'EUR', 'Standard',
             TIMESTAMP '2026-01-01 10:00:00')
            """
        )
        _assert_constraint_error(
            connection,
            """
            INSERT INTO ledger_entries VALUES
            (2, 1, 1, 1, NULL, 10.0000, 'EUR', 'Standard',
             TIMESTAMP '2026-01-01 10:00:00')
            """,
        )
        connection.execute(
            """
            INSERT INTO fx_rates VALUES
            (1, 'EUR', 'USD', DATE '2026-01-01', 1.100000,
             'Synthetic', FALSE, TIMESTAMP '2026-01-01 00:00:00')
            """
        )
        _assert_constraint_error(
            connection,
            """
            INSERT INTO fx_rates VALUES
            (2, 'EUR', 'USD', DATE '2026-01-01', 1.200000,
             'Synthetic', FALSE, TIMESTAMP '2026-01-01 00:00:00')
            """,
        )


def test_ledger_debit_credit_constraint_is_enforced() -> None:
    with duckdb.connect(database=":memory:") as connection:
        connection.execute(DDL.read_text(encoding="utf-8"))
        _insert_account(connection)
        _insert_transaction(connection)
        invalid_values = [
            "NULL, NULL",
            "10.0000, 10.0000",
            "0.0000, NULL",
            "NULL, -1.0000",
        ]
        for entry_id, values in enumerate(invalid_values, start=1):
            _assert_constraint_error(
                connection,
                f"""
                INSERT INTO ledger_entries VALUES
                ({entry_id}, 1, 1, {entry_id}, {values}, 'EUR', 'Standard',
                 TIMESTAMP '2026-01-01 10:00:00')
                """,
            )


def test_fx_lookup_is_documented_as_composite_and_non_fk() -> None:
    ddl = DDL.read_text(encoding="utf-8")
    dbml = ERD.read_text(encoding="utf-8")
    header = HEADER_SPEC.read_text(encoding="utf-8")

    for field in ("source_currency", "target_currency", "transaction_date"):
        assert f"transactions.{field}" in ddl
    assert "UNIQUE (from_currency, to_currency, rate_date)" in ddl
    assert "Ref: transactions.source_currency" not in dbml
    assert "Ref: transactions.target_currency" not in dbml
    assert "not a physical foreign key" in header


def test_required_physical_relationships_are_present() -> None:
    ddl = DDL.read_text(encoding="utf-8")

    assert "FOREIGN KEY (parent_account_id) REFERENCES accounts (account_id)" in ddl
    assert (
        "FOREIGN KEY (transaction_id) REFERENCES transactions (transaction_id)" in ddl
    )
    assert ddl.count("FOREIGN KEY (account_id) REFERENCES accounts (account_id)") == 2
    assert "FOREIGN KEY (source_currency)" not in ddl
    assert "FOREIGN KEY (target_currency)" not in ddl


def _insert_account(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """
        INSERT INTO accounts VALUES
        (1, '1000', 'Cash', 'Asset', 'Cash', 'USD', NULL, 'Debit', TRUE,
         TIMESTAMP '2025-01-01 00:00:00')
        """
    )


def _insert_transaction(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """
        INSERT INTO transactions VALUES
        (1, DATE '2026-01-01', NULL, 'ERP', 'EUR', 'USD', 10.0000, FALSE,
         TIMESTAMP '2026-01-01 10:00:00')
        """
    )


def _assert_constraint_error(
    connection: duckdb.DuckDBPyConnection,
    statement: str,
) -> None:
    try:
        connection.execute(statement)
    except duckdb.ConstraintException:
        return
    raise AssertionError("Finance DDL accepted a row that violates the contract")
