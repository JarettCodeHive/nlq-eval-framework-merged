from __future__ import annotations

import re
from pathlib import Path

import duckdb

from generators.core.schema_contract import ddl_constraints
from generators.core.schema_contract import ddl_table_specs
from generators.core.schema_contract import header_table_specs


SCHEMA_DIR = Path("schemas/project_management")
DDL = SCHEMA_DIR / "project_management_ddl.sql"
ERD = SCHEMA_DIR / "project_management_er.dbml"
HEADER_SPEC = SCHEMA_DIR / "project_management_csv_header_spec.md"
TABLE_ORDER = [
    "projects",
    "resources",
    "tasks",
    "task_resources",
    "milestones",
    "time_entries",
]
EXPECTED_PRIMARY_KEYS = {
    "projects": ("project_id",),
    "resources": ("resource_id",),
    "tasks": ("task_id",),
    "task_resources": ("task_id", "resource_id"),
    "milestones": ("milestone_id",),
    "time_entries": ("entry_id",),
}
EXPECTED_FOREIGN_KEYS = {
    ("tasks", "project_id", "projects", "project_id"),
    ("task_resources", "task_id", "tasks", "task_id"),
    ("task_resources", "resource_id", "resources", "resource_id"),
    ("milestones", "project_id", "projects", "project_id"),
    ("time_entries", "task_id", "tasks", "task_id"),
    ("time_entries", "resource_id", "resources", "resource_id"),
}
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
                    re.search(
                        r"(?:^|,\s*)not null(?:\s*,|$)",
                        attributes,
                        re.IGNORECASE,
                    )
                    is None,
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


def _dbml_keys() -> tuple[dict[str, tuple[str, ...]], set[tuple[str, str, str, str]]]:
    tables = _table_bodies(
        ERD.read_text(encoding="utf-8"),
        r"^Table ([a-z_]+) \{\n",
    )
    primary_keys: dict[str, tuple[str, ...]] = {}
    foreign_keys: set[tuple[str, str, str, str]] = set()
    for table_name, body in tables.items():
        table_primary_keys = []
        for field_name, attributes in re.findall(
            r"^  ([a-z][a-z0-9_]*) [^\n\[]+ \[([^\n]+)\]$",
            body,
            re.MULTILINE,
        ):
            if re.search(r"(?:^|,\s*)pk(?:\s*,|$)", attributes):
                table_primary_keys.append(field_name)
            reference = re.search(
                r"(?:^|,\s*)ref:\s*>\s*([a-z_]+)\.([a-z_]+)",
                attributes,
            )
            if reference:
                foreign_keys.add(
                    (
                        table_name,
                        field_name,
                        reference.group(1),
                        reference.group(2),
                    )
                )
        primary_keys[table_name] = tuple(table_primary_keys)
    return primary_keys, foreign_keys


def _header_keys() -> tuple[dict[str, tuple[str, ...]], set[tuple[str, str, str, str]]]:
    sections = _table_bodies(
        HEADER_SPEC.read_text(encoding="utf-8"),
        r"^### `([a-z_]+)\.csv`\n",
    )
    primary_keys: dict[str, tuple[str, ...]] = {}
    foreign_keys: set[tuple[str, str, str, str]] = set()
    for table_name, body in sections.items():
        table_primary_keys = []
        for field_name, key_role, reference in re.findall(
            r"^\| \d+ \| `([a-z_]+)` \| `[^`]+` \| (?:Yes|No) \|"
            r" ([^|]*) \| ([^|]*) \|$",
            body,
            re.MULTILINE,
        ):
            if "PK" in key_role:
                table_primary_keys.append(field_name)
            reference_match = re.search(r"`([a-z_]+)\.([a-z_]+)`", reference)
            if "FK" in key_role and reference_match:
                foreign_keys.add(
                    (
                        table_name,
                        field_name,
                        reference_match.group(1),
                        reference_match.group(2),
                    )
                )
        primary_keys[table_name] = tuple(table_primary_keys)
    return primary_keys, foreign_keys


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


def test_primary_and_foreign_key_contract_is_frozen() -> None:
    constraints = ddl_constraints(DDL)
    dbml_primary_keys, dbml_foreign_keys = _dbml_keys()
    header_primary_keys, header_foreign_keys = _header_keys()

    assert constraints["primary_keys"] == EXPECTED_PRIMARY_KEYS
    assert constraints["foreign_keys"] == EXPECTED_FOREIGN_KEYS
    assert dbml_primary_keys == EXPECTED_PRIMARY_KEYS
    assert dbml_foreign_keys == EXPECTED_FOREIGN_KEYS
    assert header_primary_keys == EXPECTED_PRIMARY_KEYS
    assert header_foreign_keys == EXPECTED_FOREIGN_KEYS


def test_task_resources_is_the_explicit_many_to_many_bridge() -> None:
    ddl = DDL.read_text(encoding="utf-8")
    dbml = ERD.read_text(encoding="utf-8")
    header = HEADER_SPEC.read_text(encoding="utf-8")

    assert "PRIMARY KEY (task_id, resource_id)" in ddl
    assert "FOREIGN KEY (task_id) REFERENCES tasks (task_id)" in ddl
    assert "FOREIGN KEY (resource_id) REFERENCES resources (resource_id)" in ddl
    assert "tasks -> task_resources <- resources" in dbml
    assert "explicit sixth-table junction contract" in header
    assert "valid `(task_id, resource_id)` assignment" in header


def test_release_file_order_is_frozen() -> None:
    header = HEADER_SPEC.read_text(encoding="utf-8")
    release_section = header.split("## Release File Order", maxsplit=1)[1].split(
        "## Sign-Off", maxsplit=1
    )[0]

    assert re.findall(r"^\d+\. `([a-z_]+)\.csv`$", release_section, re.MULTILINE) == (
        TABLE_ORDER
    )


def test_contract_status_is_frozen_for_implementation() -> None:
    assert "Contract v1.0" in DDL.read_text(encoding="utf-8").splitlines()[0]
    assert "STATUS: Contract v1.0" in ERD.read_text(encoding="utf-8")
    assert "Status: Contract v1.0" in HEADER_SPEC.read_text(encoding="utf-8")
