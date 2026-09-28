"""Persisted workflow tests for the Logistics dataset pipeline."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import generators.logistics.pipeline as pipeline_module
from generators.core.base import DeterministicGenerator
from generators.core.integrity import failed
from generators.core.integrity import passed
from generators.core.progress import ProgressReporter
from generators.logistics.config import settings_for_profile
from generators.logistics.pipeline import LogisticsDatasetPipeline


def test_dev_pipeline_writes_and_validates_persisted_stage_csvs(
    tmp_path: Path,
) -> None:
    settings = replace(settings_for_profile("dev"), output_path=tmp_path)
    messages: list[str] = []
    pipeline = LogisticsDatasetPipeline(
        DeterministicGenerator(settings),
        progress=ProgressReporter(output=messages.append),
    )

    output = pipeline.run()

    assert output == tmp_path / "imperfect"
    expected = {f"{name}.csv" for name in settings.table_order}
    for stage in ("base", "distributed", "imperfect"):
        assert {path.name for path in (tmp_path / stage).glob("*.csv")} == expected
    for step in range(1, 7):
        assert any(f"Step {step}/6:" in message for message in messages)


def test_full_pipeline_order_and_manifest_last(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = _full_pipeline(tmp_path)
    calls: list[str] = []
    gate = [passed("test", "passed")]
    monkeypatch.setattr(pipeline_module, "validate_logistics_config", lambda: None)
    monkeypatch.setattr(
        pipeline.row_caps,
        "validate_expected_counts",
        lambda: calls.append("expected_caps") or gate,
    )
    monkeypatch.setattr(
        pipeline.exporter,
        "export_full_profile_csvs",
        lambda: calls.append("export") or [],
    )
    for validator, label in (
        (pipeline.row_caps, "exported_caps"),
        (pipeline.fk_validator, "fk_orphans"),
        (pipeline.join_validator, "joins"),
        (pipeline.imperfection_validator, "imperfections"),
    ):
        monkeypatch.setattr(
            validator,
            "validate_csv_directory",
            lambda _path, label=label: calls.append(label) or gate,
        )
    monkeypatch.setattr(
        pipeline.hash_computer,
        "compute_exported_csv_hashes",
        lambda: calls.append("hashes") or [],
    )
    monkeypatch.setattr(
        pipeline.dictionary_generator,
        "write_release_dictionary",
        lambda: calls.append("dictionary") or tmp_path / "data_dictionary.md",
    )
    monkeypatch.setattr(
        pipeline.schema_generator,
        "write_release_schema",
        lambda: calls.append("schema") or tmp_path / "schema.sql",
    )
    monkeypatch.setattr(
        pipeline.reproducibility_validator,
        "validate_release",
        lambda: calls.append("reproducibility") or gate,
    )
    manifest = tmp_path / "manifest.json"
    monkeypatch.setattr(
        pipeline.manifest_generator,
        "write_manifest",
        lambda: calls.append("manifest") or manifest,
    )

    assert pipeline.run() == manifest
    assert calls == [
        "expected_caps",
        "export",
        "exported_caps",
        "fk_orphans",
        "joins",
        "imperfections",
        "hashes",
        "dictionary",
        "schema",
        "reproducibility",
        "manifest",
    ]


def test_full_pipeline_stops_after_failed_orphan_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = _full_pipeline(tmp_path)
    calls: list[str] = []
    gate = [passed("test", "passed")]
    monkeypatch.setattr(pipeline_module, "validate_logistics_config", lambda: None)
    monkeypatch.setattr(
        pipeline.row_caps,
        "validate_expected_counts",
        lambda: calls.append("expected_caps") or gate,
    )
    monkeypatch.setattr(
        pipeline.exporter,
        "export_full_profile_csvs",
        lambda: calls.append("export") or [],
    )
    monkeypatch.setattr(
        pipeline.row_caps,
        "validate_csv_directory",
        lambda _path: calls.append("exported_caps") or gate,
    )
    monkeypatch.setattr(
        pipeline.fk_validator,
        "validate_csv_directory",
        lambda _path: calls.append("fk")
        or [failed("orders.warehouse_id.orphan_namespace", "invalid orphan")],
    )

    with pytest.raises(ValueError, match="orphan_namespace"):
        pipeline.run()

    assert calls == ["expected_caps", "export", "exported_caps", "fk"]


def test_reproducibility_uses_an_independent_generator() -> None:
    pipeline = LogisticsDatasetPipeline.for_profile("full")

    assert pipeline.reproducibility_validator.generator is not pipeline.generator
    assert pipeline.reproducibility_validator.settings == pipeline.settings


def _full_pipeline(output: Path) -> LogisticsDatasetPipeline:
    settings = replace(settings_for_profile("full"), output_path=output)
    return LogisticsDatasetPipeline(DeterministicGenerator(settings))
