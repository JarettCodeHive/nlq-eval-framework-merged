from __future__ import annotations

from generators.common.manifest import build_distribution_metadata
from generators.common.manifest import compute_file_hashes
from generators.common.manifest import compute_sha256
from generators.common.manifest import write_manifest


def test_build_distribution_metadata_separates_config_and_effective_values() -> None:
    metadata = build_distribution_metadata(
        domain_distributions={
            "amount": {"preset": "pareto_amount", "max_amount": 750000}
        },
        effective_distributions={
            "amount": {
                "name": "pareto",
                "alpha": 1.16,
                "min_amount": 5000,
                "max_amount": 750000,
                "scale": 2,
            }
        },
        distribution_targets={
            "invoice_amount": {
                "table": "invoices",
                "field": "amount",
                "distribution": "pareto",
                "settings_key": "amount",
            },
            "derived_duration": {
                "table": "invoices",
                "anchor_field": "created_at",
            },
        },
    )

    assert metadata["amount"]["preset"] == "pareto_amount"
    assert metadata["amount"]["overrides"] == {"max_amount": 750000}
    assert metadata["amount"]["effective_parameters"]["max_amount"] == 750000
    assert set(metadata["amount"]["targets"]) == {"invoice_amount"}


def test_compute_sha256_for_known_file_content(tmp_path) -> None:
    path = tmp_path / "artifact.csv"
    path.write_bytes(b"abc")

    result = compute_sha256(path)

    assert result.path == path
    assert result.bytes == 3
    assert (
        result.sha256
        == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_compute_file_hashes_preserves_input_order(tmp_path) -> None:
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    results = compute_file_hashes([second, first])

    assert [result.path for result in results] == [second, first]


def test_write_manifest_refuses_overwrite(tmp_path) -> None:
    path = tmp_path / "manifest.json"
    write_manifest(path, {"dataset_version": "dataset-vtest"})

    assert path.read_text(encoding="utf-8").endswith("\n")
    assert '"dataset_version": "dataset-vtest"' in path.read_text(encoding="utf-8")

    try:
        write_manifest(path, {"dataset_version": "dataset-vnew"})
    except FileExistsError as exc:
        assert "overwrite" in str(exc)
    else:
        raise AssertionError("write_manifest should refuse to overwrite")
