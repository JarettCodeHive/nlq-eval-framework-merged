from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from generators.crm.hashes import CRMHashComputer


def test_crm_hash_computer_loads_full_profile_settings() -> None:
    computer = CRMHashComputer.for_profile("full")

    assert computer.settings.domain == "crm"
    assert computer.settings.profile == "full"
    assert computer.settings.output_path.name == "dataset-v1.0.0"


def test_crm_hash_computer_refuses_non_release_profile() -> None:
    computer = CRMHashComputer.for_profile("dev")

    with pytest.raises(ValueError, match="full profile"):
        computer.compute_exported_csv_hashes()


def test_crm_hash_computer_reports_missing_release_csvs(tmp_path: Path) -> None:
    computer = CRMHashComputer.for_profile("full")

    with pytest.raises(FileNotFoundError, match="export-csvs"):
        computer._csv_paths(tmp_path)


def test_crm_hash_computer_includes_exact_engagement_csv_inventory(
    tmp_path: Path,
) -> None:
    computer = CRMHashComputer.for_profile("full")
    computer.settings = replace(computer.settings, output_path=tmp_path)
    for table_name in computer.settings.table_order:
        (tmp_path / f"{table_name}.csv").write_text(
            f"{table_name}_id\n1\n",
            encoding="utf-8",
        )

    hashes = computer.compute_exported_csv_hashes()

    assert [file_hash.path.name for file_hash in hashes] == [
        "accounts.csv",
        "contacts.csv",
        "campaigns.csv",
        "contact_campaigns.csv",
        "interactions.csv",
        "support_cases.csv",
    ]
    assert all(file_hash.sha256 for file_hash in hashes)


def test_crm_hash_computer_rejects_unconfigured_csv(tmp_path: Path) -> None:
    computer = CRMHashComputer.for_profile("full")
    for table_name in computer.settings.table_order:
        (tmp_path / f"{table_name}.csv").write_text("id\n1\n", encoding="utf-8")
    (tmp_path / "opportunities.csv").write_text("id\n1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="outside the configured table contract"):
        computer._csv_paths(tmp_path)
