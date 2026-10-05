from __future__ import annotations

import csv
from dataclasses import replace
import json
from pathlib import Path
from typing import Any

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.core.integrity import IntegrityCheckResult
from generators.project_management.config import load_project_management_config
from generators.project_management.data_dictionary import (
    ProjectManagementDataDictionaryGenerator,
)
from generators.project_management.manifest import (
    ProjectManagementManifestGenerator,
)
from generators.project_management.manifest import _csv_metadata
from generators.project_management.manifest import _gate_summary
from generators.project_management.schema_sql import (
    ProjectManagementSchemaSQLGenerator,
)


def test_pm_manifest_generator_loads_full_profile_settings() -> None:
    generator = ProjectManagementManifestGenerator.for_profile("full")

    assert generator.settings.domain == "project_management"
    assert generator.settings.profile == "full"
    assert generator.settings.release_version == "v1.0.0"
    assert generator.settings.manifest_generated_at == "2026-09-24T00:00:00"


def test_pm_manifest_generator_refuses_non_release_profile() -> None:
    generator = ProjectManagementManifestGenerator.for_profile("dev")

    with pytest.raises(ValueError, match="requires the full profile"):
        generator.generate_manifest()


def test_pm_manifest_reports_missing_release_csvs(tmp_path: Path) -> None:
    generator = _manifest_for_output(tmp_path)

    with pytest.raises(FileNotFoundError, match="projects.csv"):
        generator.generate_manifest()


def test_pm_manifest_requires_support_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = _manifest_for_output(tmp_path)
    _write_contract_csvs(generator, tmp_path)
    monkeypatch.setattr(
        generator,
        "_validation_status",
        lambda: {"overall_passed": True, "gates": [], "capabilities": {}},
    )

    with pytest.raises(FileNotFoundError, match="schema SQL"):
        generator.generate_manifest()


def test_pm_manifest_includes_complete_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = _manifest_for_output(tmp_path)
    _write_contract_csvs(generator, tmp_path)
    _write_support_artifacts(generator)
    monkeypatch.setattr(
        generator,
        "_validation_status",
        lambda: {
            "overall_passed": True,
            "gates": [{"name": "row_caps", "passed": True, "checks": []}],
            "capabilities": {
                "reproducibility": {
                    "status": "pending_step_24",
                    "passed": None,
                }
            },
        },
    )
    monkeypatch.setattr(
        generator,
        "_imperfection_observations",
        lambda: {"near_duplicate_time_entries": {"expected": 1500, "observed": 1500}},
    )

    manifest: dict[str, Any] = generator.generate_manifest()
    config = load_project_management_config()

    assert manifest["manifest_schema_version"] == "1.0"
    assert manifest["domain"] == "project_management"
    assert manifest["output_path"] == ("release/v1.0.0/project_management/dataset")
    assert manifest["table_order"] == [
        "projects",
        "resources",
        "tasks",
        "task_resources",
        "milestones",
        "time_entries",
    ]
    bridge = manifest["tables"]["task_resources"]
    assert bridge["role"] == "junction"
    assert bridge["primary_key"] == ["task_id", "resource_id"]
    assert bridge["configured_rows"] == 30000
    assert bridge["fields"] == config["tables"]["task_resources"]["fields"]
    assert bridge["sha256"] == manifest["hashes"]["task_resources"]
    assert set(manifest["release_artifacts"]) == {
        "schema_sql",
        "data_dictionary",
        "canonical_ddl",
        "erd_dbml",
        "csv_header_spec",
    }
    assert manifest["release_artifacts"]["schema_sql"]["path"] == (
        "release/v1.0.0/project_management/dataset/schema.sql"
    )
    assert len(manifest["release_artifacts"]["erd_dbml"]["sha256"]) == 64
    assert manifest["decimal_policy"] == config["decimal_policy"]
    assert manifest["currency_rules"] == config["currency_rules"]
    assert manifest["business_mappings"]["currency_code"] == "USD"
    assert manifest["business_mappings"]["date_semantics"]["overdue"] == (
        "due_date < reference_today AND completed_date IS NULL"
    )
    assert set(manifest["distribution_configuration"]["settings"]) == {
        "project_budget",
        "time_entry_frequency",
        "date_clustering",
    }
    assert (
        manifest["imperfection_observations"]["near_duplicate_time_entries"]["observed"]
        == 1500
    )
    assert (
        manifest["validation_status"]["capabilities"]["reproducibility"]["status"]
        == "pending_step_24"
    )


def test_pm_manifest_rejects_header_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = _manifest_for_output(tmp_path)
    _write_contract_csvs(generator, tmp_path, omit=("tasks", "estimate_hours"))
    _write_support_artifacts(generator)
    monkeypatch.setattr(
        generator,
        "_validation_status",
        lambda: {"overall_passed": True, "gates": [], "capabilities": {}},
    )

    with pytest.raises(ValueError, match="tasks.csv header differs"):
        generator.generate_manifest()


def test_pm_manifest_rejects_support_artifact_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = _manifest_for_output(tmp_path)
    _write_contract_csvs(generator, tmp_path)
    _write_support_artifacts(generator)
    (tmp_path / "schema.sql").write_text("SELECT 1;\n", encoding="utf-8")
    monkeypatch.setattr(
        generator,
        "_validation_status",
        lambda: {"overall_passed": True, "gates": [], "capabilities": {}},
    )

    with pytest.raises(ValueError, match="canonical Project Management DDL"):
        generator.generate_manifest()


def test_pm_manifest_blocks_failed_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = _manifest_for_output(tmp_path)
    _write_contract_csvs(generator, tmp_path)
    _write_support_artifacts(generator)
    monkeypatch.setattr(
        generator,
        "_validation_status",
        lambda: {
            "overall_passed": False,
            "gates": [
                {
                    "name": "fk_integrity",
                    "passed": False,
                    "checks": [
                        {
                            "check_name": "time_entries.task_resource_membership",
                            "passed": False,
                            "message": "one invalid assignment",
                        }
                    ],
                }
            ],
            "capabilities": {},
        },
    )

    with pytest.raises(ValueError, match="task_resource_membership"):
        generator.generate_manifest()


def test_pm_manifest_write_is_atomic_and_immutable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = _manifest_for_output(tmp_path)
    monkeypatch.setattr(
        generator,
        "generate_manifest",
        lambda: {"domain": "project_management", "profile": "full"},
    )

    manifest_path = generator.write_manifest()

    assert json.loads(manifest_path.read_text(encoding="utf-8"))["domain"] == (
        "project_management"
    )
    assert not (tmp_path / "manifest.json.tmp").exists()
    with pytest.raises(FileExistsError, match="immutable release"):
        generator.write_manifest()


def test_pm_manifest_rejects_non_pm_settings() -> None:
    settings = GenerationSettings.from_config_files("finance", "full")

    with pytest.raises(ValueError, match="only supports project_management"):
        ProjectManagementManifestGenerator(DeterministicGenerator(settings))


def test_csv_metadata_counts_pm_rows_and_columns(tmp_path: Path) -> None:
    path = tmp_path / "projects.csv"
    path.write_text("project_id,project_name\n1,Project One\n", encoding="utf-8")

    assert _csv_metadata(path) == {
        "columns": ["project_id", "project_name"],
        "rows": 1,
    }


def test_gate_summary_preserves_pm_check_details() -> None:
    summary = _gate_summary(
        "example",
        [IntegrityCheckResult("example.check", True, "passed")],
    )

    assert summary == {
        "name": "example",
        "passed": True,
        "checks": [
            {
                "check_name": "example.check",
                "passed": True,
                "message": "passed",
            }
        ],
    }


def _manifest_for_output(output_path: Path) -> ProjectManagementManifestGenerator:
    generator = ProjectManagementManifestGenerator.for_profile("full")
    settings = replace(generator.settings, output_path=output_path)
    generator.settings = settings
    generator.generator.settings = settings
    return generator


def _write_contract_csvs(
    generator: ProjectManagementManifestGenerator,
    output_path: Path,
    omit: tuple[str, str] | None = None,
) -> None:
    for table_name in generator.settings.table_order:
        fields = [
            field
            for field in generator.pm_config["tables"][table_name]["fields"]
            if omit != (table_name, field["name"])
        ]
        with (output_path / f"{table_name}.csv").open(
            "w", encoding="utf-8", newline=""
        ) as csv_file:
            writer = csv.writer(csv_file, lineterminator="\n")
            writer.writerow([field["name"] for field in fields])
            writer.writerow(
                [
                    _fixture_value(table_name, field["name"], field["type"])
                    for field in fields
                ]
            )


def _fixture_value(table_name: str, field_name: str, field_type: str) -> str:
    values = {
        "status": "Active" if table_name == "projects" else "InProgress",
        "priority": "Medium",
        "role": "Developer",
        "currency_code": "USD",
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
        "due_date": "2026-06-01",
        "completed_date": "",
        "planned_date": "2026-06-01",
        "actual_date": "",
        "assigned_at": "2026-01-01",
        "released_at": "",
        "entry_date": "2026-02-01",
        "created_at": "2025-12-01T00:00:00",
        "budget_amount": "1000.00",
        "hourly_rate": "50.00",
        "allocation_pct": "100.00",
        "estimate_hours": "40.00",
        "hours": "8.00",
        "is_active": "true",
    }
    if field_name in values:
        return values[field_name]
    if field_type == "integer":
        return "1"
    if field_type == "date":
        return "2026-01-01"
    if field_type == "timestamp":
        return "2026-01-01T00:00:00"
    if field_type == "boolean":
        return "false"
    if field_type == "decimal":
        return "1.00"
    return f"{table_name}_{field_name}"


def _write_support_artifacts(
    generator: ProjectManagementManifestGenerator,
) -> None:
    ProjectManagementSchemaSQLGenerator(generator.generator).write_release_schema()
    ProjectManagementDataDictionaryGenerator(
        generator.generator
    ).write_release_dictionary()
