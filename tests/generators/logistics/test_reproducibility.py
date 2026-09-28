from __future__ import annotations

from dataclasses import replace
import importlib.util

import pytest

from generators.logistics.validators.reproducibility import (
    LogisticsArtifactFingerprint,
)
from generators.logistics.validators.reproducibility import (
    LogisticsReleaseSnapshot,
)
from generators.logistics.validators.reproducibility import (
    LogisticsReproducibilityValidator,
)


def _snapshot(
    validator: LogisticsReproducibilityValidator,
) -> LogisticsReleaseSnapshot:
    artifacts = tuple(
        LogisticsArtifactFingerprint(
            name=name,
            sha256=f"hash-{name}",
            bytes=100 + position,
            rows=10 + position if name.endswith(".csv") else None,
            columns=("id", "value") if name.endswith(".csv") else None,
        )
        for position, name in enumerate(validator._expected_artifact_names())
    )
    validation = {"overall_passed": True, "gates": []}
    return LogisticsReleaseSnapshot(
        artifacts=artifacts,
        manifest={"domain": "logistics", "validation_status": validation},
        validation_status=validation,
    )


def test_matching_snapshots_pass_in_contract_order() -> None:
    validator = LogisticsReproducibilityValidator.for_profile("full")
    snapshot = _snapshot(validator)

    results = validator.compare_snapshots(snapshot, snapshot)

    assert len(results) == 10
    assert [result.check_name for result in results[:8]] == [
        "carriers_csv.reproducible_sha256",
        "warehouses_csv.reproducible_sha256",
        "orders_csv.reproducible_sha256",
        "shipments_csv.reproducible_sha256",
        "inventory_csv.reproducible_sha256",
        "schema_sql.reproducible_sha256",
        "data_dictionary_md.reproducible_sha256",
        "manifest_json.reproducible_sha256",
    ]
    assert all(result.passed for result in results)


def test_snapshot_comparison_detects_artifact_and_payload_mismatches() -> None:
    validator = LogisticsReproducibilityValidator.for_profile("full")
    first = _snapshot(validator)
    artifacts = list(first.artifacts)
    artifacts[0] = replace(artifacts[0], sha256="different")

    artifact_results = validator.compare_snapshots(
        first,
        replace(first, artifacts=tuple(artifacts)),
    )
    manifest_results = validator.compare_snapshots(
        first,
        replace(first, manifest={"domain": "changed"}),
    )
    validation_results = validator.compare_snapshots(
        first,
        replace(first, validation_status={"overall_passed": False}),
    )

    assert not artifact_results[0].passed
    assert not next(
        result
        for result in manifest_results
        if result.check_name == "manifest.reproducible_payload"
    ).passed
    assert not next(
        result
        for result in validation_results
        if result.check_name == "validation.reproducible_outcomes"
    ).passed


def test_reproducibility_contract_and_profile_guard() -> None:
    validator = LogisticsReproducibilityValidator.for_profile("full")

    assert validator._expected_artifact_names() == (
        "carriers.csv",
        "warehouses.csv",
        "orders.csv",
        "shipments.csv",
        "inventory.csv",
        "schema.sql",
        "data_dictionary.md",
        "manifest.json",
    )
    with pytest.raises(ValueError, match="requires the full profile"):
        LogisticsReproducibilityValidator.for_profile("dev").validate_release()


@pytest.mark.skipif(
    not all(
        importlib.util.find_spec(package) is not None
        for package in ("duckdb", "numpy", "pandas", "faker")
    ),
    reason="generation dependencies are not installed",
)
def test_two_full_clean_room_logistics_releases_are_identical() -> None:
    results = LogisticsReproducibilityValidator.for_profile(
        "full"
    ).validate_release()

    assert len(results) == 10
    assert all(result.passed for result in results)
