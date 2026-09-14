from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from generators.core.manifest import FileHash
from generators.crm.validators.reproducibility import CRMReproducibilityValidator


def _dependencies_available() -> bool:
    return all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    )


def test_reproducibility_compare_hashes_passes_for_matching_hashes() -> None:
    validator = CRMReproducibilityValidator.for_profile("full")
    release_hashes = [
        FileHash(Path(f"{table}.csv"), f"hash-{table}", 10)
        for table in validator.settings.table_order
    ]
    regenerated_hashes = [
        FileHash(Path(f"tmp/{table}.csv"), f"hash-{table}", 10)
        for table in validator.settings.table_order
    ]

    results = validator.compare_hashes(release_hashes, regenerated_hashes)

    assert results
    assert len(results) == 6
    assert {result.check_name for result in results} == {
        "accounts.reproducible_sha256",
        "contacts.reproducible_sha256",
        "campaigns.reproducible_sha256",
        "contact_campaigns.reproducible_sha256",
        "interactions.reproducible_sha256",
        "support_cases.reproducible_sha256",
    }
    assert all(result.passed for result in results)


def test_reproducibility_compare_hashes_fails_for_mismatch() -> None:
    validator = CRMReproducibilityValidator.for_profile("full")
    release_hashes = [
        FileHash(Path(f"{table}.csv"), "same", 10)
        for table in validator.settings.table_order
    ]
    regenerated_hashes = [
        FileHash(Path(f"tmp/{table}.csv"), "same", 10)
        for table in validator.settings.table_order
    ]
    regenerated_hashes[0] = FileHash(Path("tmp/accounts.csv"), "different", 10)

    results = validator.compare_hashes(release_hashes, regenerated_hashes)

    assert not results[0].passed
    assert "release file=accounts.csv sha256=same" in results[0].message


def test_reproducibility_compare_hashes_fails_for_wrong_file_name() -> None:
    validator = CRMReproducibilityValidator.for_profile("full")
    release_hashes = [
        FileHash(Path(f"{table}.csv"), f"hash-{table}", 10)
        for table in validator.settings.table_order
    ]
    regenerated_hashes = [
        FileHash(Path(f"tmp/{table}.csv"), f"hash-{table}", 10)
        for table in validator.settings.table_order
    ]
    regenerated_hashes[0] = FileHash(Path("tmp/wrong.csv"), "hash-accounts", 10)

    results = validator.compare_hashes(release_hashes, regenerated_hashes)

    assert not results[0].passed
    assert "expected file=accounts.csv" in results[0].message


def test_reproducibility_refuses_non_release_profile() -> None:
    validator = CRMReproducibilityValidator.for_profile("dev")

    with pytest.raises(ValueError, match="full profile"):
        validator.validate_release()


def test_regenerated_hashes_when_dependencies_are_installed() -> None:
    if not _dependencies_available():
        pytest.skip("numpy, pandas, and Faker are not installed")

    hashes = CRMReproducibilityValidator.for_profile("dev")._regenerate_and_hash()

    assert len(hashes) == 6
    assert [file_hash.path.name for file_hash in hashes] == [
        "accounts.csv",
        "contacts.csv",
        "campaigns.csv",
        "contact_campaigns.csv",
        "interactions.csv",
        "support_cases.csv",
    ]
    assert all(file_hash.sha256 for file_hash in hashes)
