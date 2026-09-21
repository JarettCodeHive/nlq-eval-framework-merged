"""Shared parsing helpers for domain schema contracts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def configured_field_names(table_config: dict[str, Any]) -> set[str]:
    """Return configured field names for one table."""

    return {field["name"] for field in table_config["fields"]}


def primary_key_fields(table_config: dict[str, Any]) -> list[str]:
    """Normalize a scalar or composite primary key into a field list."""

    primary_key = table_config["primary_key"]
    return [primary_key] if isinstance(primary_key, str) else list(primary_key)


def configured_foreign_keys(
    domain_config: dict[str, Any],
) -> set[tuple[str, str, str, str]]:
    """Return configured foreign keys as child-to-parent field tuples."""

    return {
        (
            table_name,
            field["name"],
            field["references"]["table"],
            field["references"]["field"],
        )
        for table_name, table_config in domain_config["tables"].items()
        for field in table_config["fields"]
        if "references" in field
    }


def configured_unique_constraints(
    domain_config: dict[str, Any],
) -> set[tuple[str, tuple[str, ...]]]:
    """Return normalized single- and multi-column configured unique keys."""

    constraints: set[tuple[str, tuple[str, ...]]] = set()
    for table_name, table_config in domain_config["tables"].items():
        for field in table_config["fields"]:
            if field.get("key") == "unique":
                constraints.add((table_name, (field["name"],)))
        for fields in table_config.get("unique_constraints", []):
            constraints.add((table_name, tuple(fields)))
    return constraints


def configured_sql_type(field: dict[str, Any]) -> str:
    """Convert a configured field type into normalized SQL."""

    field_type = str(field["type"]).upper()
    if field_type in {"VARCHAR", "CHAR"}:
        return f"{field_type}({int(field['max_length'])})"
    if field_type == "DECIMAL":
        return f"DECIMAL({int(field['precision'])},{int(field['scale'])})"
    if field_type not in {"INTEGER", "BOOLEAN", "DATE", "TIMESTAMP"}:
        raise ValueError(f"Unsupported configured field type: {field['type']}")
    return field_type


def normalized_default(value: Any) -> str | None:
    """Normalize configured and DDL defaults for direct comparison."""

    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).upper()
    return str(value).strip("'").upper()


def ddl_table_specs(schema_path: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """Parse ordered DDL columns and their core schema metadata."""

    field_pattern = re.compile(
        r"^(\w+)\s+"
        r"(INTEGER|BOOLEAN|DATE|TIMESTAMP|VARCHAR\(\d+\)|CHAR\(\d+\)|"
        r"DECIMAL\(\d+,\s*\d+\))(?=\s|$)(.*)$",
        re.I,
    )
    default_pattern = re.compile(
        r"\bDEFAULT\s+('(?:[^']|'')*'|TRUE|FALSE|[-+]?\d+)", re.I
    )
    tables: dict[str, dict[str, dict[str, Any]]] = {}
    for table_name, body in ddl_table_bodies(schema_path):
        fields: dict[str, dict[str, Any]] = {}
        for raw_line in body.splitlines():
            line = raw_line.strip().rstrip(",")
            match = field_pattern.match(line)
            if not match:
                continue
            field_name, sql_type, remainder = match.groups()
            default_match = default_pattern.search(remainder)
            fields[field_name] = {
                "type": re.sub(r"\s+", "", sql_type.upper()),
                "nullable": "NOT NULL" not in remainder.upper(),
                "default": (
                    normalized_default(default_match.group(1))
                    if default_match
                    else None
                ),
            }
        tables[table_name] = fields
    return tables


def ddl_table_bodies(schema_path: Path) -> list[tuple[str, str]]:
    """Extract ordered CREATE TABLE names and bodies from a DDL file."""

    ddl = schema_path.read_text(encoding="utf-8")
    tables = re.findall(
        r"^[ \t]*CREATE TABLE\s+(\w+)\s*\((.*?)^[ \t]*\);",
        ddl,
        re.S | re.I | re.M,
    )
    if not tables:
        raise ValueError(f"No CREATE TABLE statements found in {schema_path}")
    return tables


def ddl_constraints(schema_path: Path) -> dict[str, Any]:
    """Execute DDL in DuckDB and return normalized schema constraints.

    ``unique_keys`` retains the original single-column API used by CRM and
    Sales. ``unique_constraints`` exposes every UNIQUE constraint in one
    normalized shape, while ``composite_unique_keys`` provides the multi-column
    subset needed by domains such as Finance.
    """

    try:
        import duckdb
    except ImportError as exc:
        raise ImportError("DuckDB is required to validate executable schemas") from exc

    connection = duckdb.connect(":memory:")
    try:
        connection.execute(schema_path.read_text(encoding="utf-8"))
        rows = connection.execute(
            """
            SELECT table_name, constraint_type, constraint_text,
                   constraint_column_names
            FROM duckdb_constraints()
            WHERE schema_name = 'main'
            ORDER BY table_oid, constraint_index
            """
        ).fetchall()
    finally:
        connection.close()

    primary_keys: dict[str, tuple[str, ...]] = {}
    unique_constraints: set[tuple[str, tuple[str, ...]]] = set()
    checks: dict[str, set[str]] = {}
    for table_name, constraint_type, _constraint_text, column_names in rows:
        if constraint_type == "PRIMARY KEY":
            primary_keys[table_name] = tuple(column_names)
        elif constraint_type == "UNIQUE":
            unique_constraints.add((table_name, tuple(column_names)))
        elif constraint_type == "CHECK":
            checks.setdefault(table_name, set()).add(compact_sql(_constraint_text))
    unique_keys = {
        (table_name, columns[0])
        for table_name, columns in unique_constraints
        if len(columns) == 1
    }
    composite_unique_keys = {
        (table_name, columns)
        for table_name, columns in unique_constraints
        if len(columns) > 1
    }
    return {
        "primary_keys": primary_keys,
        "unique_keys": unique_keys,
        "unique_constraints": unique_constraints,
        "composite_unique_keys": composite_unique_keys,
        "foreign_keys": _ddl_foreign_keys(schema_path),
        "checks": checks,
    }


def _ddl_foreign_keys(schema_path: Path) -> set[tuple[str, str, str, str]]:
    """Parse physical FK field pairs, including self and composite references."""

    foreign_keys: set[tuple[str, str, str, str]] = set()
    pattern = re.compile(
        r"FOREIGN\s+KEY\s*\(([^)]+)\)\s*" r"REFERENCES\s+(\w+)\s*\(([^)]+)\)",
        re.I,
    )
    for child_table, body in ddl_table_bodies(schema_path):
        for child_fields, parent_table, parent_fields in pattern.findall(body):
            children = _identifier_list(child_fields)
            parents = _identifier_list(parent_fields)
            if len(children) != len(parents):
                raise ValueError(
                    "DDL foreign key has different child and parent field counts: "
                    f"{child_table}({children}) -> {parent_table}({parents})"
                )
            foreign_keys.update(
                (child_table, child, parent_table, parent)
                for child, parent in zip(children, parents, strict=True)
            )
    return foreign_keys


def _identifier_list(value: str) -> tuple[str, ...]:
    """Normalize a comma-separated SQL identifier list."""

    identifiers = tuple(identifier.strip() for identifier in value.split(","))
    if not identifiers or any(not re.fullmatch(r"\w+", name) for name in identifiers):
        raise ValueError(f"Unsupported SQL identifier list: {value}")
    return identifiers


def header_table_specs(
    header_path: Path,
) -> dict[str, list[dict[str, Any]]]:
    """Parse ordered field contracts from a Markdown CSV header spec."""

    text = header_path.read_text(encoding="utf-8")
    matches = list(re.finditer(r"^### `([a-z_]+)\.csv`\n", text, re.MULTILINE))
    tables: dict[str, list[dict[str, Any]]] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end() : end]
        fields = [
            {
                "name": name,
                "type": re.sub(r"\s+", "", sql_type.upper()),
                "nullable": nullable == "Yes",
            }
            for name, sql_type, nullable in re.findall(
                r"^\| \d+ \| `([a-z_]+)` \| `([^`]+)` \| (Yes|No) \|",
                body,
                re.MULTILINE,
            )
        ]
        header_match = re.search(r"```csv\n([^\n]+)\n```", body)
        if not fields or header_match is None:
            raise ValueError(f"Incomplete CSV header contract for {match.group(1)}")
        if header_match.group(1).split(",") != [field["name"] for field in fields]:
            raise ValueError(f"CSV header row differs from table for {match.group(1)}")
        tables[match.group(1)] = fields
    if not tables:
        raise ValueError(f"No CSV table contracts found in {header_path}")
    return tables


def compact_sql(value: str) -> str:
    """Normalize SQL text for whitespace-insensitive comparisons."""

    return re.sub(r"[\s()]", "", value).lower()
