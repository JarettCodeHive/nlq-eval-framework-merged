from __future__ import annotations

from dataclasses import replace
import importlib.util

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.finance.validators.reproducibility import (
    FinanceArtifactFingerprint,
)
from generators.finance.validators.reproducibility import FinanceReleaseSnapshot
from generators.finance.validators.reproducibility import (
    FinanceReproducibilityValidator,
)


def _dependencies_available() -> bool:
    return all(
        importlib.util.find_spec(package) is not None
        for package in ("duckdb", "numpy", "pandas", "faker")
    )


def _snapshot(
    validator: FinanceReproducibilityValidator,
) -> FinanceReleaseSnapshot:
    artifacts = tuple(
        FinanceArtifactFingerprint(
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
        "gates": [{"name": "accounting", "passed": True, "checks": []}],
    }
    return FinanceReleaseSnapshot(
        artifacts=artifacts,
        manifest={
            "domain": "finance",
            "dataset_version": "dataset-v1.0.0",
            "validation_status": validation,
        },
        validation_status=validation,
    )


def test_matching_finance_snapshots_pass_in_contract_order() -> None:
    validator = FinanceReproducibilityValidator.for_profile("full")
    first = _snapshot(validator)
    second = _snapshot(validator)

    results = validator.compare_snapshots(first, second)

    assert len(results) == 10
    assert [result.check_name for result in results[:8]] == [
        "accounts_csv.reproducible_sha256",
        "transactions_csv.reproducible_sha256",
        "ledger_entries_csv.reproducible_sha256",
        "budgets_csv.reproducible_sha256",
        "fx_rates_csv.reproducible_sha256",
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
def test_finance_snapshot_comparison_detects_artifact_mismatch(
    change: dict,
    expected_text: str,
) -> None:
    validator = FinanceReproducibilityValidator.for_profile("full")
    first = _snapshot(validator)
    artifacts = list(first.artifacts)
    artifacts[0] = replace(artifacts[0], **change)
    second = replace(first, artifacts=tuple(artifacts))

    results = validator.compare_snapshots(first, second)

    assert not results[0].passed
    assert expected_text in results[0].message


def test_finance_snapshot_comparison_detects_file_count_mismatch() -> None:
    validator = FinanceReproducibilityValidator.for_profile("full")
    first = _snapshot(validator)
    second = replace(first, artifacts=first.artifacts[:-1])

    results = validator.compare_snapshots(first, second)

    assert len(results) == 1
    assert results[0].check_name == "reproducibility.file_count"
    assert not results[0].passed


def test_finance_snapshot_comparison_detects_complete_manifest_mismatch() -> None:
    validator = FinanceReproducibilityValidator.for_profile("full")
    first = _snapshot(validator)
    second = replace(first, manifest={**first.manifest, "seed": 99})

    results = validator.compare_snapshots(first, second)

    manifest_result = next(
        result
        for result in results
        if result.check_name == "manifest.reproducible_payload"
    )
    assert not manifest_result.passed


def test_finance_snapshot_comparison_detects_validation_outcome_mismatch() -> None:
    validator = FinanceReproducibilityValidator.for_profile("full")
    first = _snapshot(validator)
    second = replace(
        first,
        validation_status={"overall_passed": False, "gates": []},
    )

    results = validator.compare_snapshots(first, second)

    validation_result = next(
        result
        for result in results
        if result.check_name == "validation.reproducible_outcomes"
    )
    assert not validation_result.passed


def test_finance_reproducibility_refuses_non_release_profile() -> None:
    validator = FinanceReproducibilityValidator.for_profile("dev")

    with pytest.raises(ValueError, match="requires the full profile"):
        validator.validate_release()


def test_finance_reproducibility_uses_isolated_expected_artifact_contract() -> None:
    validator = FinanceReproducibilityValidator.for_profile("full")

    assert validator._expected_artifact_names() == (
        "accounts.csv",
        "transactions.csv",
        "ledger_entries.csv",
        "budgets.csv",
        "fx_rates.csv",
        "schema.sql",
        "data_dictionary.md",
        "manifest.json",
    )


@pytest.mark.skipif(
    not _dependencies_available(),
    reason="duckdb, numpy, pandas, and Faker are not installed",
)
def test_two_full_clean_room_finance_releases_are_identical() -> None:
    results = FinanceReproducibilityValidator.for_profile("full").validate_release()

    assert len(results) == 10
    assert all(result.passed for result in results)


def test_finance_reproducibility_rejects_non_finance_settings() -> None:
    settings = GenerationSettings.from_config_files("sales", "full")

    with pytest.raises(ValueError, match="only supports finance"):
        FinanceReproducibilityValidator(DeterministicGenerator(settings))
