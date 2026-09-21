from __future__ import annotations

import re
from pathlib import Path

import duckdb


SCHEMA_DIR = Path("schemas/sales")
DDL = SCHEMA_DIR / "sales_ddl.sql"
ERD = SCHEMA_DIR / "sales_er.dbml"
HEADER_SPEC = SCHEMA_DIR / "sales_csv_header_spec.md"
TABLE_ORDER = ["leads", "deals", "products", "quotations", "targets"]
CURRENCY_TABLES = ["deals", "products", "targets"]

SQL_TYPE = (
    r"INTEGER|VARCHAR(?:\(\d+\))?|CHAR\(\d+\)|"
    r"DECIMAL\(\d+,\s*\d+\)|BOOLEAN|DATE|TIMESTAMP"
)


def _normalized_default(value: str | None) -> str | None:
    """Normalize case-insensitive SQL keywords while preserving literals."""

    if value is None or value.startswith("'"):
        return value
    return value.upper()


def _table_bodies(text: str, start_pattern: str) -> dict[str, str]:
    matches = list(re.finditer(start_pattern, text, re.MULTILINE))
    return {
        match.group(1): text[match.end() : matches[index + 1].start()]
        if index + 1 < len(matches)
        else text[match.end() :]
        for index, match in enumerate(matches)
    }


def _ddl_contract() -> dict[str, list[tuple[str, str, bool, str | None]]]:
    tables = _table_bodies(
        DDL.read_text(encoding="utf-8"),
        r"^CREATE TABLE ([a-z_]+) \(\n",
    )
    contract: dict[str, list[tuple[str, str, bool, str | None]]] = {}
    for table_name, body in tables.items():
        fields = []
        for name, sql_type, remainder in re.findall(
            rf"^    ([a-z][a-z0-9_]*) ({SQL_TYPE})([^\n]*)(?:,)?$",
            body,
            re.MULTILINE | re.IGNORECASE,
        ):
            default_match = re.search(r"\bDEFAULT\s+('[^']+'|\w+)", remainder)
            fields.append(
                (
                    name,
                    sql_type.replace(" ", "").upper(),
                    "NOT NULL" not in remainder.upper(),
                    _normalized_default(
                        default_match.group(1) if default_match else None
                    ),
                )
            )
        contract[table_name] = fields
    return contract


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


def _header_sections() -> dict[str, str]:
    return _table_bodies(
        HEADER_SPEC.read_text(encoding="utf-8"),
        r"^### `([a-z_]+)\.csv`\n",
    )


def _header_contract(section: str) -> list[tuple[str, str, bool]]:
    return [
        (name, sql_type.replace(" ", "").upper(), nullable == "Yes")
        for name, sql_type, nullable in re.findall(
            r"^\| \d+ \| `([a-z_]+)` \| `([^`]+)` \| (Yes|No) \|",
            section,
            re.MULTILINE,
        )
    ]


def test_ddl_dbml_and_header_contracts_are_aligned() -> None:
    ddl = _ddl_contract()
    dbml = _dbml_contract()
    headers = _header_sections()

    assert list(ddl) == TABLE_ORDER
    assert list(dbml) == TABLE_ORDER
    assert list(headers) == TABLE_ORDER
    for table_name in TABLE_ORDER:
        assert dbml[table_name] == ddl[table_name]
        assert _header_contract(headers[table_name]) == [
            (name, sql_type, nullable)
            for name, sql_type, nullable, _ in ddl[table_name]
        ]
        header_match = re.search(r"```csv\n([^\n]+)\n```", headers[table_name])
        assert header_match is not None
        assert header_match.group(1).split(",") == [
            field[0] for field in ddl[table_name]
        ]


def test_ddl_executes_in_duckdb_with_expected_tables() -> None:
    with duckdb.connect(database=":memory:") as connection:
        connection.execute(DDL.read_text(encoding="utf-8"))
        assert [row[0] for row in connection.execute("SHOW TABLES").fetchall()] == sorted(
            TABLE_ORDER
        )


def test_currency_contract_is_strictly_usd_only() -> None:
    ddl = DDL.read_text(encoding="utf-8")
    dbml = ERD.read_text(encoding="utf-8")

    assert len(re.findall(r"CHECK \(currency_code = 'USD'\)", ddl)) == len(
        CURRENCY_TABLES
    )
    assert len(re.findall(r"note: 'Fixed value: USD'", dbml)) == len(
        CURRENCY_TABLES
    )

    with duckdb.connect(database=":memory:") as connection:
        connection.execute(ddl)
        connection.execute(
            "INSERT INTO products "
            "(product_id, sku, product_name, currency_code, is_active, created_at) "
            "VALUES (1, 'SKU-1', 'Product 1', 'USD', TRUE, CURRENT_TIMESTAMP)"
        )
        try:
            connection.execute(
                "INSERT INTO products "
                "(product_id, sku, product_name, currency_code, is_active, created_at) "
                "VALUES (2, 'SKU-2', 'Product 2', 'EUR', TRUE, CURRENT_TIMESTAMP)"
            )
        except duckdb.ConstraintException:
            pass
        else:
            raise AssertionError("Sales DDL accepted a non-USD currency")


def test_quota_join_is_documented_as_analytical_not_physical() -> None:
    ddl = DDL.read_text(encoding="utf-8")
    dbml = ERD.read_text(encoding="utf-8")
    header = HEADER_SPEC.read_text(encoding="utf-8")

    assert "deals.rep_name = targets.rep_name" in ddl
    assert "deals.close_date >= targets.period_start" in ddl
    assert "deals.close_date < targets.period_end" in ddl
    assert "Ref: deals.rep_name" not in dbml
    assert "not a foreign key" in dbml
    assert "Analytical key" in header


def test_required_sales_relationships_are_present() -> None:
    ddl = DDL.read_text(encoding="utf-8")

    assert "FOREIGN KEY (lead_id) REFERENCES leads (lead_id)" in ddl
    assert "FOREIGN KEY (deal_id) REFERENCES deals (deal_id)" in ddl
    assert "FOREIGN KEY (product_id) REFERENCES products (product_id)" in ddl
    assert "FOREIGN KEY (rep_name)" not in ddl
