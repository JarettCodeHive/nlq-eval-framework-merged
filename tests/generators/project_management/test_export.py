from __future__ import annotations

import csv
from dataclasses import replace
import importlib.util
from pathlib import Path

import pytest

from generators.project_management.export import ProjectManagementCSVExporter
from generators.project_management.generator import (
    PROJECT_MANAGEMENT_COLUMN_CONTRACTS,
)


pytestmark = pytest.mark.skipif(
    not all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    ),
    reason="numpy, pandas, and Faker are not installed",
)


@pytest.fixture(scope="module")
def dev_stages() -> dict:
    return ProjectManagementCSVExporter.for_profile(
        "dev"
    ).generate_validated_stages()


def test_exporter_uses_versioned_full_output() -> None:
    exporter = ProjectManagementCSVExporter.for_profile("full")

    assert exporter.settings.is_release_profile
    assert exporter.output_dir.name == "dataset-v1.0.0"


def test_profile_specific_export_methods_reject_wrong_profile() -> None:
    with pytest.raises(ValueError, match="requires the full profile"):
        ProjectManagementCSVExporter.for_profile(
            "dev"
        ).export_full_profile_csvs()
    with pytest.raises(ValueError, match="only for dev"):
        ProjectManagementCSVExporter.for_profile("full").export_dev_previews()


def test_dev_previews_write_three_contract_stages(
    tmp_path: Path,
    dev_stages: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = ProjectManagementCSVExporter.for_profile("dev")
    exporter.settings = replace(exporter.settings, output_path=tmp_path)
    monkeypatch.setattr(exporter, "generate_validated_stages", lambda: dev_stages)

    results = exporter.export_dev_previews()

    assert list(results) == ["base", "distributed", "imperfect"]
    expected_rows = [10, 10, 100, 300, 25, 1500]
    assert [result.rows for result in results["base"]] == expected_rows
    assert [result.rows for result in results["distributed"]] == expected_rows
    assert [result.rows for result in results["imperfect"]] == [
        10,
        10,
        100,
        300,
        25,
        1515,
    ]
    for stage_name in results:
        assert {path.name for path in (tmp_path / stage_name).glob("*.csv")} == {
            f"{name}.csv" for name in PROJECT_MANAGEMENT_COLUMN_CONTRACTS
        }


def test_export_contract_is_platform_neutral_and_deterministic(
    tmp_path: Path,
    dev_stages: dict,
) -> None:
    exporter = ProjectManagementCSVExporter.for_profile("dev")
    first = tmp_path / "first"
    second = tmp_path / "second"
    exporter.export_tables(dev_stages["imperfect"], output_dir=first)
    exporter.export_tables(dev_stages["imperfect"], output_dir=second)

    for table_name, columns in PROJECT_MANAGEMENT_COLUMN_CONTRACTS.items():
        first_path = first / f"{table_name}.csv"
        assert first_path.read_bytes() == (second / f"{table_name}.csv").read_bytes()
        assert not first_path.read_bytes().startswith(b"\xef\xbb\xbf")
        assert b"\r\n" not in first_path.read_bytes()
        with first_path.open(encoding="utf-8", newline="") as csv_file:
            assert next(csv.reader(csv_file)) == columns


def test_export_removes_stale_csvs(
    tmp_path: Path,
    dev_stages: dict,
) -> None:
    exporter = ProjectManagementCSVExporter.for_profile("dev")
    destination = tmp_path / "imperfect"
    destination.mkdir()
    (destination / "retired.csv").write_text("old\n", encoding="utf-8")

    exporter.export_tables(dev_stages["imperfect"], output_dir=destination)

    assert not (destination / "retired.csv").exists()


def test_release_export_refuses_manifest_sealed_destination(tmp_path: Path) -> None:
    exporter = ProjectManagementCSVExporter.for_profile("full")
    exporter.settings = replace(exporter.settings, output_path=tmp_path)
    (tmp_path / "manifest.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="immutable release"):
        exporter.export_full_profile_csvs()


def test_full_export_writes_only_final_tables(
    tmp_path: Path,
    dev_stages: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = ProjectManagementCSVExporter.for_profile("full")
    exporter.settings = replace(exporter.settings, output_path=tmp_path)
    monkeypatch.setattr(exporter, "generate_validated_stages", lambda: dev_stages)

    results = exporter.export_full_profile_csvs()

    assert [result.table_name for result in results] == list(
        PROJECT_MANAGEMENT_COLUMN_CONTRACTS
    )
    assert not (tmp_path / "base").exists()
    assert not (tmp_path / "distributed").exists()
    assert not (tmp_path / "imperfect").exists()
    assert {path.name for path in tmp_path.glob("*.csv")} == {
        f"{name}.csv" for name in PROJECT_MANAGEMENT_COLUMN_CONTRACTS
    }
