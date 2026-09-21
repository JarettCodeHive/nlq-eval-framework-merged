from __future__ import annotations

import csv
from dataclasses import replace
import importlib.util
from pathlib import Path

import pytest

from generators.sales.export import SalesCSVExporter
from generators.sales.generator import SALES_COLUMN_CONTRACTS


pytestmark = pytest.mark.skipif(
    not all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    ),
    reason="numpy, pandas, and Faker are not installed",
)


def test_sales_exporter_uses_versioned_full_output() -> None:
    exporter = SalesCSVExporter.for_profile("full")

    assert exporter.settings.is_release_profile
    assert exporter.output_dir.name == "dataset-v1.0.0"


def test_release_export_rejects_dev_profile() -> None:
    with pytest.raises(ValueError, match="requires the full profile"):
        SalesCSVExporter.for_profile("dev").export_full_profile_csvs()


def test_preview_export_rejects_full_profile() -> None:
    with pytest.raises(ValueError, match="only for dev"):
        SalesCSVExporter.for_profile("full").export_dev_previews()


def test_dev_previews_write_all_three_validated_stages(tmp_path: Path) -> None:
    exporter = SalesCSVExporter.for_profile("dev")
    exporter.settings = replace(exporter.settings, output_path=tmp_path)
    results = exporter.export_dev_previews()

    assert list(results) == ["base", "distributed", "imperfect"]
    for stage_name, stage_results in results.items():
        assert [result.table_name for result in stage_results] == list(
            SALES_COLUMN_CONTRACTS
        )
        assert {path.name for path in (tmp_path / stage_name).glob("*.csv")} == {
            f"{table_name}.csv" for table_name in SALES_COLUMN_CONTRACTS
        }
    assert results["base"][3].rows == 300
    assert results["distributed"][3].rows == 300
    assert results["imperfect"][3].rows == 303


def test_preview_csv_contract_is_platform_neutral(tmp_path: Path) -> None:
    exporter = SalesCSVExporter.for_profile("dev")
    exporter.settings = replace(exporter.settings, output_path=tmp_path)
    exporter.export_dev_previews()

    product_path = tmp_path / "imperfect" / "products.csv"
    raw = product_path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in raw

    with product_path.open(encoding="utf-8", newline="") as csv_file:
        rows = list(csv.DictReader(csv_file))
    assert list(rows[0]) == SALES_COLUMN_CONTRACTS["products"]
    assert {row["is_active"] for row in rows} <= {"true", "false"}
    assert sum(row["list_price"] == "" for row in rows) == 2
    populated_prices = [row["list_price"] for row in rows if row["list_price"]]
    assert all(len(value.rsplit(".", 1)[1]) == 2 for value in populated_prices)


def test_export_removes_stale_csvs_from_destination(tmp_path: Path) -> None:
    exporter = SalesCSVExporter.for_profile("dev")
    exporter.settings = replace(exporter.settings, output_path=tmp_path)
    stages = exporter.generate_validated_stages()
    destination = tmp_path / "imperfect"
    destination.mkdir()
    (destination / "retired.csv").write_text("old\n", encoding="utf-8")

    exporter.export_tables(stages["imperfect"], output_dir=destination)

    assert not (destination / "retired.csv").exists()
    assert {path.name for path in destination.glob("*.csv")} == {
        f"{table_name}.csv" for table_name in SALES_COLUMN_CONTRACTS
    }


def test_release_export_refuses_manifest_sealed_destination(tmp_path: Path) -> None:
    exporter = SalesCSVExporter.for_profile("full")
    exporter.settings = replace(exporter.settings, output_path=tmp_path)
    (tmp_path / "manifest.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="immutable release"):
        exporter.export_full_profile_csvs()


def test_full_release_export_runs_final_validation_and_writes_contract(
    tmp_path: Path,
) -> None:
    exporter = SalesCSVExporter.for_profile("full")
    exporter.settings = replace(exporter.settings, output_path=tmp_path)
    results = exporter.export_full_profile_csvs()

    assert [result.table_name for result in results] == list(SALES_COLUMN_CONTRACTS)
    assert [result.rows for result in results] == [15000, 5000, 500, 30300, 400]
    for table_name, columns in SALES_COLUMN_CONTRACTS.items():
        with (tmp_path / f"{table_name}.csv").open(
            encoding="utf-8", newline=""
        ) as csv_file:
            assert next(csv.reader(csv_file)) == columns
