from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path

import pytest

from generators.core.base import PROJECT_ROOT
from generators.logistics.export import LogisticsCSVExporter
from generators.logistics.generator import LOGISTICS_COLUMN_CONTRACTS


@pytest.fixture(scope="module")
def dev_stages() -> dict:
    return LogisticsCSVExporter.for_profile("dev").generate_validated_stages()


def test_profile_specific_export_methods_reject_wrong_profile() -> None:
    with pytest.raises(ValueError, match="requires the full profile"):
        LogisticsCSVExporter.for_profile("dev").export_full_profile_csvs()
    with pytest.raises(ValueError, match="only for dev"):
        LogisticsCSVExporter.for_profile("full").export_dev_previews()


def test_dev_previews_write_all_three_stages(
    tmp_path: Path,
    dev_stages: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = LogisticsCSVExporter.for_profile("dev")
    exporter.settings = replace(exporter.settings, output_path=tmp_path)
    monkeypatch.setattr(exporter, "generate_validated_stages", lambda: dev_stages)

    results = exporter.export_dev_previews()

    assert list(results) == ["base", "distributed", "imperfect"]
    assert [result.rows for result in results["base"]] == [10, 10, 1000, 1500, 500]
    assert [result.rows for result in results["distributed"]] == [
        10,
        10,
        1000,
        1500,
        500,
    ]
    assert [result.rows for result in results["imperfect"]] == [
        10,
        10,
        1000,
        1515,
        500,
    ]


def test_export_is_platform_neutral_and_deterministic(
    tmp_path: Path,
    dev_stages: dict,
) -> None:
    exporter = LogisticsCSVExporter.for_profile("dev")
    first = tmp_path / "first"
    second = tmp_path / "second"
    exporter.export_tables(dev_stages["imperfect"], output_dir=first)
    exporter.export_tables(dev_stages["imperfect"], output_dir=second)

    for table_name, columns in LOGISTICS_COLUMN_CONTRACTS.items():
        first_path = first / f"{table_name}.csv"
        content = first_path.read_bytes()
        assert content == (second / f"{table_name}.csv").read_bytes()
        assert not content.startswith(b"\xef\xbb\xbf")
        assert b"\r\n" not in content
        with first_path.open(encoding="utf-8", newline="") as csv_file:
            assert next(csv.reader(csv_file)) == columns


def test_export_removes_stale_unconfigured_csv(
    tmp_path: Path,
    dev_stages: dict,
) -> None:
    exporter = LogisticsCSVExporter.for_profile("dev")
    destination = tmp_path / "imperfect"
    destination.mkdir()
    (destination / "retired.csv").write_text("old\n", encoding="utf-8")

    exporter.export_tables(dev_stages["imperfect"], output_dir=destination)

    assert not (destination / "retired.csv").exists()


def test_dev_export_rejects_release_tree(dev_stages: dict) -> None:
    exporter = LogisticsCSVExporter.for_profile("dev")

    with pytest.raises(ValueError, match="cannot use release paths"):
        exporter.export_tables(
            dev_stages["imperfect"],
            output_dir=PROJECT_ROOT / "release" / "logistics" / "dev-invalid",
        )


def test_release_export_refuses_manifest_sealed_destination(tmp_path: Path) -> None:
    exporter = LogisticsCSVExporter.for_profile("full")
    exporter.settings = replace(exporter.settings, output_path=tmp_path)
    (tmp_path / "manifest.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="immutable release"):
        exporter.export_full_profile_csvs()


def test_full_export_writes_only_final_tables(
    tmp_path: Path,
    dev_stages: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = LogisticsCSVExporter.for_profile("full")
    exporter.settings = replace(exporter.settings, output_path=tmp_path)
    monkeypatch.setattr(exporter, "generate_validated_stages", lambda: dev_stages)

    results = exporter.export_full_profile_csvs()

    assert [result.table_name for result in results] == list(
        LOGISTICS_COLUMN_CONTRACTS
    )
    assert not (tmp_path / "base").exists()
    assert not (tmp_path / "distributed").exists()
    assert not (tmp_path / "imperfect").exists()
    assert {path.name for path in tmp_path.glob("*.csv")} == {
        f"{name}.csv" for name in LOGISTICS_COLUMN_CONTRACTS
    }
