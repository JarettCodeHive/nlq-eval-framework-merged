from __future__ import annotations

import csv
from dataclasses import replace
import json
from pathlib import Path
from typing import Any

import pytest

from generators.logistics.config import load_logistics_config
from generators.logistics.data_dictionary import LogisticsDataDictionaryGenerator
from generators.logistics.manifest import LogisticsManifestGenerator


def test_manifest_refuses_non_release_profile() -> None:
    with pytest.raises(ValueError, match="requires the full profile"):
        LogisticsManifestGenerator.for_profile("dev").generate_manifest()


def test_manifest_includes_complete_logistics_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = _manifest_for_output(tmp_path)
    _write_contract_csvs(generator, tmp_path)
    _write_support_artifacts(generator)
    monkeypatch.setattr(generator, "_validation_status", _passing_validation)

    manifest = generator.generate_manifest()
    config = load_logistics_config()

    assert manifest["domain"] == "logistics"
    assert manifest["output_path"] == "release/logistics/v1.0.0/dataset"
    assert manifest["table_order"] == config["table_order"]
    assert manifest["tables"]["shipments"]["role"] == "relationship_fact"
    assert manifest["tables"]["orders"]["configured_rows"] == 100000
    assert manifest["relationships"] == config["relationships"]
    assert manifest["orphan_exclusions"] == config["orphan_exclusions"]
    assert set(manifest["release_artifacts"]) == {
        "schema_sql",
        "data_dictionary",
        "canonical_ddl",
        "erd_dbml",
        "csv_header_spec",
        "semantic_contract",
    }
    assert set(manifest["distribution_configuration"]["settings"]) == {
        "order_total",
        "shipment_frequency",
        "date_clustering",
    }
    observations = manifest["imperfection_observations"]
    assert "near_duplicate_shipments" in observations
    assert "missing_order_warehouses" in observations
    assert "orphaned_order_warehouses" in observations
    assert "order_total_outliers" in observations
    assert "coordinated_boundary_dates" in observations


def test_manifest_rejects_header_and_support_artifact_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = _manifest_for_output(tmp_path)
    _write_contract_csvs(generator, tmp_path, omit=("orders", "order_priority"))
    _write_support_artifacts(generator)
    monkeypatch.setattr(generator, "_validation_status", _passing_validation)

    with pytest.raises(ValueError, match="orders.csv header differs"):
        generator.generate_manifest()

    _write_contract_csvs(generator, tmp_path)
    (tmp_path / "schema.sql").write_text("SELECT 1;\n", encoding="utf-8")
    with pytest.raises(ValueError, match="canonical Logistics DDL"):
        generator.generate_manifest()


def test_manifest_blocks_failed_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = _manifest_for_output(tmp_path)
    _write_contract_csvs(generator, tmp_path)
    _write_support_artifacts(generator)
    monkeypatch.setattr(
        generator,
        "_validation_status",
        lambda: {
            "overall_passed": False,
            "gates": [
                {
                    "name": "fk_integrity",
                    "passed": False,
                    "checks": [
                        {
                            "check_name": "orders.warehouse_id.orphan_namespace",
                            "passed": False,
                            "message": "invalid orphan",
                        }
                    ],
                }
            ],
            "capabilities": {},
        },
    )

    with pytest.raises(ValueError, match="orphan_namespace"):
        generator.generate_manifest()


def test_manifest_write_is_atomic_and_immutable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = _manifest_for_output(tmp_path)
    monkeypatch.setattr(
        generator,
        "generate_manifest",
        lambda: {"domain": "logistics", "profile": "full"},
    )

    path = generator.write_manifest()

    assert json.loads(path.read_text(encoding="utf-8"))["domain"] == "logistics"
    assert not (tmp_path / "manifest.json.tmp").exists()
    with pytest.raises(FileExistsError, match="immutable release"):
        generator.write_manifest()


def _manifest_for_output(output: Path) -> LogisticsManifestGenerator:
    generator = LogisticsManifestGenerator.for_profile("full")
    settings = replace(generator.settings, output_path=output)
    generator.settings = settings
    generator.generator.settings = settings
    return generator


def _passing_validation() -> dict[str, Any]:
    return {
        "overall_passed": True,
        "gates": [],
        "capabilities": {
            "reproducibility": {
                "status": "pending_step_24",
                "passed": None,
            }
        },
    }


def _write_contract_csvs(
    generator: LogisticsManifestGenerator,
    output: Path,
    omit: tuple[str, str] | None = None,
) -> None:
    for table_name in generator.settings.table_order:
        fields = [
            field
            for field in generator.logistics_config["tables"][table_name]["fields"]
            if omit != (table_name, field["name"])
        ]
        with (output / f"{table_name}.csv").open(
            "w", encoding="utf-8", newline=""
        ) as csv_file:
            writer = csv.writer(csv_file, lineterminator="\n")
            writer.writerow([field["name"] for field in fields])
            writer.writerow(
                [
                    _fixture_value(table_name, field["name"], field["type"])
                    for field in fields
                ]
            )


def _fixture_value(table_name: str, name: str, field_type: str) -> str:
    values = {
        "warehouse_id": "1",
        "order_id": "1",
        "carrier_id": "1",
        "tracking_number": "TRK000000000001",
        "order_date": "2026-01-01",
        "ship_date": "2026-01-02",
        "delivery_date": "2026-01-03",
        "last_updated_at": "2026-01-03T00:00:00",
        "created_at": "2025-12-01T00:00:00",
        "total_amount": "100.00",
        "currency_code": "USD",
        "status": "Delivered" if table_name != "orders" else "Delivered",
    }
    if name in values:
        return values[name]
    if field_type == "integer":
        return "1"
    if field_type == "decimal":
        return "1.00"
    if field_type == "boolean":
        return "true"
    if field_type == "date":
        return "2026-01-01"
    if field_type == "timestamp":
        return "2026-01-01T00:00:00"
    return f"{table_name}_{name}"


def _write_support_artifacts(generator: LogisticsManifestGenerator) -> None:
    output = generator.settings.output_path
    (output / "schema.sql").write_bytes(generator.settings.schema_source.read_bytes())
    dictionary = LogisticsDataDictionaryGenerator(generator.generator)
    (output / "data_dictionary.md").write_text(
        dictionary.generate_markdown(), encoding="utf-8", newline="\n"
    )
