from __future__ import annotations

import csv
from dataclasses import replace
import importlib.util
from pathlib import Path
import re

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.finance.export import FinanceCSVExporter
from generators.finance.generator import FINANCE_COLUMN_CONTRACTS


pytestmark = pytest.mark.skipif(
    not all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    ),
    reason="numpy, pandas, and Faker are not installed",
)


@pytest.fixture(scope="module")
def dev_stages() -> dict:
    return FinanceCSVExporter.for_profile("dev").generate_validated_stages()


def test_finance_exporter_uses_versioned_full_output() -> None:
    exporter = FinanceCSVExporter.for_profile("full")

    assert exporter.settings.is_release_profile
    assert exporter.output_dir.name == "dataset-v1.0.0"


def test_release_export_rejects_dev_profile() -> None:
    with pytest.raises(ValueError, match="requires the full profile"):
        FinanceCSVExporter.for_profile("dev").export_full_profile_csvs()


def test_preview_export_rejects_full_profile() -> None:
    with pytest.raises(ValueError, match="only for dev"):
        FinanceCSVExporter.for_profile("full").export_dev_previews()


def test_dev_previews_write_all_three_validated_stages(tmp_path: Path) -> None:
    exporter = FinanceCSVExporter.for_profile("dev")
    exporter.settings = replace(exporter.settings, output_path=tmp_path)
    results = exporter.export_dev_previews()

    assert list(results) == ["base", "distributed", "imperfect"]
    for stage_name, stage_results in results.items():
        assert [result.table_name for result in stage_results] == list(
            FINANCE_COLUMN_CONTRACTS
        )
        assert {path.name for path in (tmp_path / stage_name).glob("*.csv")} == {
            f"{table_name}.csv" for table_name in FINANCE_COLUMN_CONTRACTS
        }
    assert [result.rows for result in results["base"]] == [50, 1000, 2000, 100, 370]
    assert [result.rows for result in results["distributed"]] == [
        50,
        1000,
        2000,
        100,
        370,
    ]
    assert [result.rows for result in results["imperfect"]] == [
        50,
        1000,
        2000,
        101,
        370,
    ]


def test_preview_csv_contract_is_platform_neutral(tmp_path: Path) -> None:
    exporter = FinanceCSVExporter.for_profile("dev")
    exporter.settings = replace(exporter.settings, output_path=tmp_path)
    exporter.export_dev_previews()

    for table_name, columns in FINANCE_COLUMN_CONTRACTS.items():
        path = tmp_path / "imperfect" / f"{table_name}.csv"
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf")
        assert b"\r\n" not in raw
        with path.open(encoding="utf-8", newline="") as csv_file:
            assert next(csv.reader(csv_file)) == columns

    with (tmp_path / "imperfect" / "accounts.csv").open(
        encoding="utf-8", newline=""
    ) as csv_file:
        accounts = list(csv.DictReader(csv_file))
    assert {row["is_active"] for row in accounts} <= {"true", "false"}

    with (tmp_path / "imperfect" / "transactions.csv").open(
        encoding="utf-8", newline=""
    ) as csv_file:
        transactions = list(csv.DictReader(csv_file))
    assert {row["reversed"] for row in transactions} <= {"true", "false"}
    assert all(re.fullmatch(r"\d+\.\d{4}", row["total_amount"]) for row in transactions)
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["transaction_date"]) for row in transactions)
    assert all(
        re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", row["posted_at"])
        for row in transactions
    )

    with (tmp_path / "imperfect" / "fx_rates.csv").open(
        encoding="utf-8", newline=""
    ) as csv_file:
        fx_rates = list(csv.DictReader(csv_file))
    assert sum(row["rate"] == "" for row in fx_rates) == 10
    assert all(
        not row["rate"] or re.fullmatch(r"\d+\.\d{6}", row["rate"])
        for row in fx_rates
    )


def test_export_is_byte_deterministic(
    tmp_path: Path,
    dev_stages: dict,
) -> None:
    exporter = FinanceCSVExporter.for_profile("dev")
    first = tmp_path / "first"
    second = tmp_path / "second"

    exporter.export_tables(dev_stages["imperfect"], output_dir=first)
    exporter.export_tables(dev_stages["imperfect"], output_dir=second)

    for table_name in FINANCE_COLUMN_CONTRACTS:
        assert (first / f"{table_name}.csv").read_bytes() == (
            second / f"{table_name}.csv"
        ).read_bytes()


def test_export_removes_stale_csvs_from_destination(
    tmp_path: Path,
    dev_stages: dict,
) -> None:
    exporter = FinanceCSVExporter.for_profile("dev")
    destination = tmp_path / "imperfect"
    destination.mkdir()
    (destination / "retired.csv").write_text("old\n", encoding="utf-8")

    exporter.export_tables(dev_stages["imperfect"], output_dir=destination)

    assert not (destination / "retired.csv").exists()
    assert {path.name for path in destination.glob("*.csv")} == {
        f"{table_name}.csv" for table_name in FINANCE_COLUMN_CONTRACTS
    }


def test_release_export_refuses_manifest_sealed_destination(tmp_path: Path) -> None:
    exporter = FinanceCSVExporter.for_profile("full")
    exporter.settings = replace(exporter.settings, output_path=tmp_path)
    (tmp_path / "manifest.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="immutable release"):
        exporter.export_full_profile_csvs()


def test_full_export_writes_only_final_tables(
    tmp_path: Path,
    dev_stages: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = FinanceCSVExporter.for_profile("full")
    exporter.settings = replace(exporter.settings, output_path=tmp_path)
    monkeypatch.setattr(exporter, "generate_validated_stages", lambda: dev_stages)

    results = exporter.export_full_profile_csvs()

    assert [result.table_name for result in results] == list(FINANCE_COLUMN_CONTRACTS)
    assert not (tmp_path / "base").exists()
    assert not (tmp_path / "distributed").exists()
    assert not (tmp_path / "imperfect").exists()
    assert {path.name for path in tmp_path.glob("*.csv")} == {
        f"{table_name}.csv" for table_name in FINANCE_COLUMN_CONTRACTS
    }


def test_exporter_rejects_non_finance_settings() -> None:
    settings = GenerationSettings.from_config_files("sales", "dev")
    with pytest.raises(ValueError, match="only supports finance"):
        FinanceCSVExporter(DeterministicGenerator(settings))
