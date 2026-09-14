from __future__ import annotations

import re
from pathlib import Path

from generators.crm.config import load_crm_config
from generators.crm.validators.config import _configured_sql_type
from generators.crm.validators.config import validate_crm_config


SCHEMA_DIR = Path("schemas/crm")
HEADER_SPEC = SCHEMA_DIR / "crm_csv_header_spec.md"
ERD = SCHEMA_DIR / "crm_er.dbml"
SUMMARY = SCHEMA_DIR / "crm_schema_summary.md"


def _header_sections() -> dict[str, str]:
    text = HEADER_SPEC.read_text(encoding="utf-8")
    return {
        table_name: body
        for table_name, body in re.findall(
            r"^## `([a-z_]+)\.csv`\n(.*?)(?=^## |\Z)",
            text,
            re.MULTILINE | re.DOTALL,
        )
    }


def _documented_header(section: str) -> list[str]:
    match = re.search(r"```csv\n([^\n]+)\n```", section)
    assert match is not None
    return match.group(1).split(",")


def _documented_fields(section: str) -> list[tuple[str, str, bool]]:
    return [
        (name, sql_type.replace(" ", ""), nullable == "Yes")
        for name, sql_type, nullable in re.findall(
            r"^\| \d+ \| `([a-z_]+)` \| `([^`]+)` \| (Yes|No) \|",
            section,
            re.MULTILINE,
        )
    ]


def _dbml_table_fields() -> dict[str, list[str]]:
    field_pattern = re.compile(
        r"^  ([a-z][a-z0-9_]*)\s+"
        r"(?:integer|varchar(?:\(\d+\))?|char\(\d+\)|"
        r"decimal\(\d+,\s*\d+\)|boolean|date|timestamp)(?=\s|\[|$)",
        re.IGNORECASE,
    )
    tables: dict[str, list[str]] = {}
    current_table: str | None = None
    for line in ERD.read_text(encoding="utf-8").splitlines():
        table_match = re.match(r"^Table ([a-z_]+) \{$", line)
        if table_match:
            current_table = table_match.group(1)
            tables[current_table] = []
            continue
        if current_table is None:
            continue
        field_match = field_pattern.match(line)
        if field_match:
            tables[current_table].append(field_match.group(1))
    return tables


def test_ddl_and_config_contracts_are_aligned() -> None:
    validate_crm_config()


def test_header_spec_matches_config_names_types_and_nullability() -> None:
    crm_config = load_crm_config()
    sections = _header_sections()

    assert list(sections) == crm_config["table_order"]
    for table_name in crm_config["table_order"]:
        configured_fields = crm_config["tables"][table_name]["fields"]
        configured_header = [field["name"] for field in configured_fields]
        assert _documented_header(sections[table_name]) == configured_header
        assert _documented_fields(sections[table_name]) == [
            (
                field["name"],
                _configured_sql_type(field),
                bool(field["nullable"]),
            )
            for field in configured_fields
        ]


def test_erd_table_and_field_order_matches_config() -> None:
    crm_config = load_crm_config()
    dbml_tables = _dbml_table_fields()

    assert list(dbml_tables) == crm_config["table_order"]
    for table_name in crm_config["table_order"]:
        assert dbml_tables[table_name] == [
            field["name"] for field in crm_config["tables"][table_name]["fields"]
        ]


def test_schema_summary_covers_every_current_table() -> None:
    crm_config = load_crm_config()
    summary = SUMMARY.read_text(encoding="utf-8")

    for table_name in crm_config["table_order"]:
        assert f"### `{table_name}`" in summary


def test_stale_schema_copies_are_absent() -> None:
    assert not (SCHEMA_DIR / "crm_ddl copy.sql").exists()
    assert not (SCHEMA_DIR / "crm_er copy.dbml").exists()
