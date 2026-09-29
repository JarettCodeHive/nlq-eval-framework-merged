from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from generators.logistics.hashes import LogisticsHashComputer


EXPECTED_FILES = [
    "carriers.csv",
    "warehouses.csv",
    "orders.csv",
    "shipments.csv",
    "inventory.csv",
]


def test_hash_computer_refuses_non_release_profile() -> None:
    with pytest.raises(ValueError, match="requires the full profile"):
        LogisticsHashComputer.for_profile("dev").compute_exported_csv_hashes()


def test_hashes_preserve_contract_order_and_hash_exact_bytes(
    tmp_path: Path,
) -> None:
    computer = LogisticsHashComputer.for_profile("full")
    computer.settings = replace(computer.settings, output_path=tmp_path)
    payloads: dict[str, bytes] = {}
    for position, table_name in enumerate(computer.settings.table_order, start=1):
        payload = f"id,value\n{position},Logistics {position}\n".encode()
        payloads[f"{table_name}.csv"] = payload
        (tmp_path / f"{table_name}.csv").write_bytes(payload)

    hashes = computer.compute_exported_csv_hashes()

    assert [item.path.name for item in hashes] == EXPECTED_FILES
    for item in hashes:
        payload = payloads[item.path.name]
        assert item.bytes == len(payload)
        assert item.sha256 == sha256(payload).hexdigest()


def test_hash_computer_reports_missing_and_unexpected_csvs(
    tmp_path: Path,
) -> None:
    computer = LogisticsHashComputer.for_profile("full")
    with pytest.raises(FileNotFoundError, match="carriers.csv"):
        computer._csv_paths(tmp_path)

    for table_name in computer.settings.table_order:
        (tmp_path / f"{table_name}.csv").write_text("id\n1\n", encoding="utf-8")
    (tmp_path / "unexpected.csv").write_text("id\n1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="outside the configured table contract"):
        computer._csv_paths(tmp_path)
