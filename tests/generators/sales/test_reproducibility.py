from __future__ import annotations

from dataclasses import replace
import importlib.util
from pathlib import Path

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.sales.export import SalesCSVExporter
from generators.sales.validators.reproducibility import SalesCSVFingerprint
from generators.sales.validators.reproducibility import (
    SalesReproducibilityValidator,
)


def _dependencies_available() -> bool:
    return all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    )


def _fingerprints(
    validator: SalesReproducibilityValidator,
) -> list[SalesCSVFingerprint]:
    return [
        SalesCSVFingerprint(
            path=Path(f"{table_name}.csv"),
            sha256=f"hash-{table_name}",
            bytes=100 + position,
            rows=10 + position,
            columns=(f"{table_name}_id", "value"),
        )
        for position, table_name in enumerate(validator.settings.table_order)
    ]


def test_matching_sales_fingerprints_pass_in_configured_order() -> None:
    validator = SalesReproducibilityValidator.for_profile("full")
    release = _fingerprints(validator)
    regenerated = [replace(item, path=Path("tmp") / item.path) for item in release]

    results = validator.compare_fingerprints(release, regenerated)

    assert len(results) == 5
    assert [result.check_name for result in results] == [
        "leads.reproducible_sha256",
        "deals.reproducible_sha256",
        "products.reproducible_sha256",
        "quotations.reproducible_sha256",
        "targets.reproducible_sha256",
    ]
    assert all(result.passed for result in results)


@pytest.mark.parametrize(
    ("change", "expected_text"),
    [
        ({"sha256": "different"}, "sha256=different"),
        ({"bytes": 999}, "bytes=999"),
        ({"rows": 999}, "rows=999"),
        ({"columns": ("wrong",)}, "columns=['wrong']"),
        ({"path": Path("tmp/wrong.csv")}, "file=wrong.csv"),
    ],
)
def test_sales_fingerprint_comparison_detects_each_mismatch(
    change: dict,
    expected_text: str,
) -> None:
    validator = SalesReproducibilityValidator.for_profile("full")
    release = _fingerprints(validator)
    regenerated = [replace(item, path=Path("tmp") / item.path) for item in release]
    regenerated[0] = replace(regenerated[0], **change)

    results = validator.compare_fingerprints(release, regenerated)

    assert not results[0].passed
    assert expected_text in results[0].message


def test_sales_fingerprint_comparison_detects_file_count_mismatch() -> None:
    validator = SalesReproducibilityValidator.for_profile("full")
    release = _fingerprints(validator)

    results = validator.compare_fingerprints(release, release[:-1])

    assert len(results) == 1
    assert results[0].check_name == "reproducibility.file_count"
    assert not results[0].passed


def test_sales_reproducibility_refuses_non_release_profile() -> None:
    validator = SalesReproducibilityValidator.for_profile("dev")

    with pytest.raises(ValueError, match="requires the full profile"):
        validator.validate_release()


@pytest.mark.skipif(
    not _dependencies_available(),
    reason="numpy, pandas, and Faker are not installed",
)
def test_clean_regeneration_returns_complete_dev_fingerprints() -> None:
    validator = SalesReproducibilityValidator.for_profile("dev")

    fingerprints = validator._regenerate_and_fingerprint()

    assert [item.path.name for item in fingerprints] == [
        "leads.csv",
        "deals.csv",
        "products.csv",
        "quotations.csv",
        "targets.csv",
    ]
    assert [item.rows for item in fingerprints] == [150, 50, 50, 303, 40]
    assert all(len(item.sha256) == 64 for item in fingerprints)


@pytest.mark.skipif(
    not _dependencies_available(),
    reason="numpy, pandas, and Faker are not installed",
)
def test_full_release_matches_clean_regeneration_without_modification(
    tmp_path: Path,
) -> None:
    exporter = SalesCSVExporter.for_profile("full")
    settings = replace(exporter.settings, output_path=tmp_path)
    exporter.settings = settings
    exporter.generator.settings = settings
    exporter.export_full_profile_csvs()
    before = {path.name: path.read_bytes() for path in sorted(tmp_path.glob("*.csv"))}
    validator = SalesReproducibilityValidator(exporter.generator)

    results = validator.validate_release()

    after = {path.name: path.read_bytes() for path in sorted(tmp_path.glob("*.csv"))}
    assert len(results) == 5
    assert all(result.passed for result in results)
    assert after == before


def test_sales_reproducibility_rejects_non_sales_settings() -> None:
    settings = GenerationSettings.from_config_files("crm", "full")

    with pytest.raises(ValueError, match="only supports sales"):
        SalesReproducibilityValidator(DeterministicGenerator(settings))
