from __future__ import annotations

from pathlib import Path

from generators.core.schema_contract import configured_unique_constraints
from generators.core.schema_contract import ddl_constraints


def test_ddl_constraints_support_single_and_composite_unique_keys(
    tmp_path: Path,
) -> None:
    ddl_path = tmp_path / "schema.sql"
    ddl_path.write_text(
        """
        CREATE TABLE nodes (
            node_id INTEGER NOT NULL,
            node_code VARCHAR(32) NOT NULL,
            parent_node_id INTEGER,
            CONSTRAINT pk_nodes PRIMARY KEY (node_id),
            CONSTRAINT uq_nodes_code UNIQUE (node_code),
            CONSTRAINT uq_nodes_id_code UNIQUE (node_id, node_code),
            CONSTRAINT fk_nodes_parent
            FOREIGN KEY (parent_node_id) REFERENCES nodes (node_id)
        );

        CREATE TABLE lines (
            line_id INTEGER NOT NULL,
            node_id INTEGER NOT NULL,
            node_code VARCHAR(32) NOT NULL,
            line_number INTEGER NOT NULL,
            CONSTRAINT pk_lines PRIMARY KEY (line_id),
            CONSTRAINT uq_lines_node_number UNIQUE (node_id, line_number),
            CONSTRAINT fk_lines_node
            FOREIGN KEY (node_id, node_code) REFERENCES nodes (node_id, node_code)
        );
        """,
        encoding="utf-8",
    )

    constraints = ddl_constraints(ddl_path)

    assert constraints["unique_keys"] == {("nodes", "node_code")}
    assert constraints["composite_unique_keys"] == {
        ("lines", ("node_id", "line_number")),
        ("nodes", ("node_id", "node_code")),
    }
    assert constraints["unique_constraints"] == {
        ("nodes", ("node_code",)),
        ("lines", ("node_id", "line_number")),
        ("nodes", ("node_id", "node_code")),
    }
    assert constraints["foreign_keys"] == {
        ("nodes", "parent_node_id", "nodes", "node_id"),
        ("lines", "node_id", "nodes", "node_id"),
        ("lines", "node_code", "nodes", "node_code"),
    }


def test_configured_unique_constraints_normalize_field_and_table_keys() -> None:
    config = {
        "tables": {
            "accounts": {
                "fields": [
                    {"name": "account_id"},
                    {"name": "account_number", "key": "unique"},
                ]
            },
            "fx_rates": {
                "fields": [
                    {"name": "from_currency"},
                    {"name": "to_currency"},
                    {"name": "rate_date"},
                ],
                "unique_constraints": [["from_currency", "to_currency", "rate_date"]],
            },
        }
    }

    assert configured_unique_constraints(config) == {
        ("accounts", ("account_number",)),
        ("fx_rates", ("from_currency", "to_currency", "rate_date")),
    }
