from __future__ import annotations

from dataclasses import replace
import importlib.util

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.project_management.validators.reproducibility import (
    ProjectManagementArtifactFingerprint,
)
from generators.project_management.validators.reproducibility import (
    ProjectManagementReleaseSnapshot,
)
from generators.project_management.validators.reproducibility import (
    ProjectManagementReproducibilityValidator,
)


def _dependencies_available() -> bool:
    return all(
        importlib.util.find_spec(package) is not None
        for package in ("duckdb", "numpy", "pandas", "faker")
    )


def _snapshot(
    validator: ProjectManagementReproducibilityValidator,
) -> ProjectManagementReleaseSnapshot:
    artifacts = tuple(
        ProjectManagementArtifactFingerprint(
            name=name,
            sha256=f"hash-{name}",
            bytes=100 + position,
            rows=(10 + position if name.endswith(".csv") else None),
            columns=(("id", "value") if name.endswith(".csv") else None),
        )
        for position, name in enumerate(validator._expected_artifact_names())
    )
    validation = {
        "overall_passed": True,
        "gates": [{"name": "relational", "passed": True, "checks": []}],
    }
    return ProjectManagementReleaseSnapshot(
        artifacts=artifacts,
        manifest={
            "domain": "project_management",
            "dataset_version": "dataset-v1.0.0",
            "validation_status": validation,
        },
        validation_status=validation,
    )


def test_matching_pm_snapshots_pass_in_contract_order() -> None:
    validator = ProjectManagementReproducibilityValidator.for_profile("full")
    first = _snapshot(validator)
    second = _snapshot(validator)

    results = validator.compare_snapshots(first, second)

    assert len(results) == 11
    assert [result.check_name for result in results[:9]] == [
        "projects_csv.reproducible_sha256",
        "resources_csv.reproducible_sha256",
        "tasks_csv.reproducible_sha256",
        "task_resources_csv.reproducible_sha256",
        "milestones_csv.reproducible_sha256",
        "time_entries_csv.reproducible_sha256",
        "schema_sql.reproducible_sha256",
        "data_dictionary_md.reproducible_sha256",
        "manifest_json.reproducible_sha256",
    ]
    assert results[-2].check_name == "manifest.reproducible_payload"
    assert results[-1].check_name == "validation.reproducible_outcomes"
    assert all(result.passed for result in results)


@pytest.mark.parametrize(
    ("change", "expected_text"),
    [
        ({"sha256": "different"}, "sha256=different"),
        ({"bytes": 999}, "bytes=999"),
        ({"rows": 999}, "rows=999"),
        ({"columns": ("wrong",)}, "columns=('wrong',)"),
        ({"name": "wrong.csv"}, "file=wrong.csv"),
    ],
)
def test_pm_snapshot_comparison_detects_artifact_mismatch(
    change: dict,
    expected_text: str,
) -> None:
    validator = ProjectManagementReproducibilityValidator.for_profile("full")
    first = _snapshot(validator)
    artifacts = list(first.artifacts)
    artifacts[0] = replace(artifacts[0], **change)
    second = replace(first, artifacts=tuple(artifacts))

    results = validator.compare_snapshots(first, second)

    assert not results[0].passed
    assert expected_text in results[0].message


def test_pm_snapshot_comparison_detects_file_count_mismatch() -> None:
    validator = ProjectManagementReproducibilityValidator.for_profile("full")
    first = _snapshot(validator)
    second = replace(first, artifacts=first.artifacts[:-1])

    results = validator.compare_snapshots(first, second)

    assert len(results) == 1
    assert results[0].check_name == "reproducibility.file_count"
    assert not results[0].passed


def test_pm_snapshot_comparison_detects_complete_manifest_mismatch() -> None:
    validator = ProjectManagementReproducibilityValidator.for_profile("full")
    first = _snapshot(validator)
    second = replace(first, manifest={**first.manifest, "seed": 99})

    results = validator.compare_snapshots(first, second)

    result = next(
        item for item in results if item.check_name == "manifest.reproducible_payload"
    )
    assert not result.passed


def test_pm_snapshot_comparison_detects_validation_outcome_mismatch() -> None:
    validator = ProjectManagementReproducibilityValidator.for_profile("full")
    first = _snapshot(validator)
    second = replace(
        first,
        validation_status={"overall_passed": False, "gates": []},
    )

    results = validator.compare_snapshots(first, second)

    result = next(
        item
        for item in results
        if item.check_name == "validation.reproducible_outcomes"
    )
    assert not result.passed


def test_pm_reproducibility_refuses_non_release_profile() -> None:
    validator = ProjectManagementReproducibilityValidator.for_profile("dev")

    with pytest.raises(ValueError, match="requires the full profile"):
        validator.validate_release()


def test_pm_reproducibility_uses_complete_artifact_contract() -> None:
    validator = ProjectManagementReproducibilityValidator.for_profile("full")

    assert validator._expected_artifact_names() == (
        "projects.csv",
        "resources.csv",
        "tasks.csv",
        "task_resources.csv",
        "milestones.csv",
        "time_entries.csv",
        "schema.sql",
        "data_dictionary.md",
        "manifest.json",
    )


@pytest.mark.skipif(
    not _dependencies_available(),
    reason="duckdb, numpy, pandas, and Faker are not installed",
)
def test_two_full_clean_room_pm_releases_are_identical() -> None:
    results = ProjectManagementReproducibilityValidator.for_profile(
        "full"
    ).validate_release()

    assert len(results) == 11
    assert all(result.passed for result in results)


def test_pm_reproducibility_rejects_non_pm_settings() -> None:
    settings = GenerationSettings.from_config_files("finance", "full")

    with pytest.raises(ValueError, match="only supports project_management"):
        ProjectManagementReproducibilityValidator(DeterministicGenerator(settings))
