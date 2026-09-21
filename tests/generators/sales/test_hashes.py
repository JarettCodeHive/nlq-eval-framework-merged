from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.sales.hashes import SalesHashComputer


def test_sales_hash_computer_loads_full_profile_settings() -> None:
    computer = SalesHashComputer.for_profile("full")

    assert computer.settings.domain == "sales"
    assert computer.settings.profile == "full"
    assert computer.settings.output_path.name == "dataset-v1.0.0"


def test_sales_hash_computer_refuses_non_release_profile() -> None:
    computer = SalesHashComputer.for_profile("dev")

    with pytest.raises(ValueError, match="requires the full profile"):
        computer.compute_exported_csv_hashes()


def test_sales_hash_computer_reports_missing_release_csvs(tmp_path: Path) -> None:
    computer = SalesHashComputer.for_profile("full")

    with pytest.raises(FileNotFoundError, match="leads.csv"):
        computer._csv_paths(tmp_path)


def test_sales_hashes_preserve_contract_order_and_hash_exact_bytes(
    tmp_path: Path,
) -> None:
    computer = SalesHashComputer.for_profile("full")
    computer.settings = replace(computer.settings, output_path=tmp_path)
    payloads: dict[str, bytes] = {}
    for position, table_name in enumerate(computer.settings.table_order, start=1):
        payload = f"{table_name}_id,value\n{position},Sales {position}\n".encode()
        payloads[f"{table_name}.csv"] = payload
        (tmp_path / f"{table_name}.csv").write_bytes(payload)

    hashes = computer.compute_exported_csv_hashes()

    assert [item.path.name for item in hashes] == [
        "leads.csv",
        "deals.csv",
        "products.csv",
        "quotations.csv",
        "targets.csv",
    ]
    for item in hashes:
        payload = payloads[item.path.name]
        assert item.bytes == len(payload)
        assert item.sha256 == sha256(payload).hexdigest()
        assert len(item.sha256) == 64


def test_sales_hash_computer_rejects_unconfigured_csv(tmp_path: Path) -> None:
    computer = SalesHashComputer.for_profile("full")
    for table_name in computer.settings.table_order:
        (tmp_path / f"{table_name}.csv").write_text("id\n1\n", encoding="utf-8")
    (tmp_path / "retired.csv").write_text("id\n1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="outside the configured table contract"):
        computer._csv_paths(tmp_path)


def test_sales_hash_computer_rejects_non_sales_settings() -> None:
    settings = GenerationSettings.from_config_files("crm", "full")

    with pytest.raises(ValueError, match="only supports sales"):
        SalesHashComputer(DeterministicGenerator(settings))
