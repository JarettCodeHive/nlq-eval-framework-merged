"""End-to-end workflow tests for the Finance dataset pipeline."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import generators.finance.pipeline as pipeline_module
from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.core.integrity import passed
from generators.core.progress import ProgressReporter
from generators.finance.config import settings_for_profile
from generators.finance.pipeline import FinanceDatasetPipeline


def test_dev_pipeline_writes_and_validates_persisted_stage_csvs(
    tmp_path: Path,
) -> None:
    settings = replace(settings_for_profile("dev"), output_path=tmp_path)
    messages: list[str] = []
    pipeline = FinanceDatasetPipeline(
        DeterministicGenerator(settings),
        progress=ProgressReporter(output=messages.append),
    )

    output_path = pipeline.run()

    assert output_path == tmp_path / "imperfect"
    for stage in ("base", "distributed", "imperfect"):
        stage_dir = tmp_path / stage
        assert {path.name for path in stage_dir.glob("*.csv")} == {
            f"{table_name}.csv" for table_name in settings.table_order
        }
    for step in range(1, 7):
        assert any(f"Step {step}/6:" in message for message in messages)
    assert messages[-1].endswith(str(output_path))


def test_full_pipeline_runs_release_steps_and_writes_manifest_last(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = replace(settings_for_profile("full"), output_path=tmp_path)
    messages: list[str] = []
    pipeline = FinanceDatasetPipeline(
        DeterministicGenerator(settings),
        progress=ProgressReporter(output=messages.append),
    )
    calls: list[str] = []
    gate = [passed("test", "passed")]

    monkeypatch.setattr(pipeline_module, "validate_finance_config", lambda: None)
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
        lambda _path: calls.append("fk_accounting") or gate,
    )
    monkeypatch.setattr(
        pipeline.join_validator,
        "validate_csv_directory",
        lambda _path: calls.append("joins_fx") or gate,
    )
    monkeypatch.setattr(
        pipeline.imperfection_validator,
        "validate_csv_directory",
        lambda _path: calls.append("imperfections") or gate,
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
    manifest_path = tmp_path / "manifest.json"
    monkeypatch.setattr(
        pipeline.manifest_generator,
        "write_manifest",
        lambda: calls.append("manifest") or manifest_path,
    )

    output_path = pipeline.run()

    assert output_path == manifest_path
    assert calls == [
        "expected_caps",
        "export",
        "exported_caps",
        "fk_accounting",
        "joins_fx",
        "imperfections",
        "hashes",
        "dictionary",
        "schema",
        "reproducibility",
        "manifest",
    ]
    assert calls[-1] == "manifest"
    for step in range(1, 12):
        assert any(f"Step {step}/11:" in message for message in messages)


def test_pipeline_rejects_non_finance_settings() -> None:
    settings = GenerationSettings.from_config_files("sales", "dev")

    with pytest.raises(ValueError, match="only supports finance"):
        FinanceDatasetPipeline(DeterministicGenerator(settings))


def test_reproducibility_uses_an_independent_generator() -> None:
    pipeline = FinanceDatasetPipeline.for_profile("full")

    assert pipeline.reproducibility_validator.generator is not pipeline.generator
    assert (
        pipeline.reproducibility_validator.settings
        == pipeline.exporter.settings
        == pipeline.settings
    )
