from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import duckdb
import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.core.manifest import compute_sha256
from generators.core.schema_contract import ddl_constraints
from generators.core.schema_contract import ddl_table_specs
from generators.project_management.schema_sql import (
    ProjectManagementSchemaSQLGenerator,
)


EXPECTED_TABLE_ORDER = [
    "projects",
    "resources",
    "tasks",
    "task_resources",
    "milestones",
    "time_entries",
]


def test_schema_sql_matches_canonical_ddl_contract() -> None:
    generator = ProjectManagementSchemaSQLGenerator.for_profile("full")
    sql = generator.generate_sql()
    specifications = ddl_table_specs(generator.settings.schema_source)

    assert sql == generator.settings.schema_source.read_text(encoding="utf-8")
    assert sql.encode("utf-8") == generator.settings.schema_source.read_bytes()
    assert list(specifications) == EXPECTED_TABLE_ORDER
    assert specifications["projects"]["budget_amount"]["type"] == "DECIMAL(15,2)"
    assert specifications["tasks"]["due_date"]["nullable"]
    assert specifications["task_resources"]["allocation_pct"]["default"] == "100"
    assert specifications["time_entries"]["hours"]["type"] == "DECIMAL(6,2)"


def test_schema_sql_preserves_primary_and_foreign_keys() -> None:
    generator = ProjectManagementSchemaSQLGenerator.for_profile("full")
    constraints = ddl_constraints(generator.settings.schema_source)

    assert constraints["primary_keys"]["task_resources"] == (
        "task_id",
        "resource_id",
    )
    assert constraints["foreign_keys"] == {
        ("tasks", "project_id", "projects", "project_id"),
        ("task_resources", "task_id", "tasks", "task_id"),
        ("task_resources", "resource_id", "resources", "resource_id"),
        ("milestones", "project_id", "projects", "project_id"),
        ("time_entries", "task_id", "tasks", "task_id"),
        ("time_entries", "resource_id", "resources", "resource_id"),
    }


def test_schema_sql_executes_in_duckdb() -> None:
    sql = ProjectManagementSchemaSQLGenerator.for_profile("full").generate_sql()

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


def test_schema_sql_refuses_non_release_profile() -> None:
    with pytest.raises(ValueError, match="requires the full profile"):
        ProjectManagementSchemaSQLGenerator.for_profile(
            "dev"
        ).write_release_schema()


def test_schema_sql_writes_byte_identical_artifact(tmp_path: Path) -> None:
    generator = ProjectManagementSchemaSQLGenerator.for_profile("full")
    generator.settings = replace(generator.settings, output_path=tmp_path)

    output_path = generator.write_release_schema()

    assert output_path == tmp_path / "schema.sql"
    assert output_path.read_bytes() == generator.settings.schema_source.read_bytes()
    assert compute_sha256(output_path).sha256 == compute_sha256(
        generator.settings.schema_source
    ).sha256
    assert not (tmp_path / "schema.sql.tmp").exists()


def test_schema_sql_refuses_sealed_release(tmp_path: Path) -> None:
    generator = ProjectManagementSchemaSQLGenerator.for_profile("full")
    generator.settings = replace(generator.settings, output_path=tmp_path)
    (tmp_path / "manifest.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="immutable release"):
        generator.write_release_schema()


def test_schema_sql_rejects_non_pm_settings() -> None:
    settings = GenerationSettings.from_config_files("sales", "full")

    with pytest.raises(ValueError, match="only supports project_management"):
        ProjectManagementSchemaSQLGenerator(DeterministicGenerator(settings))
