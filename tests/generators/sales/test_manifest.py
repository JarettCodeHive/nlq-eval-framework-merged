from __future__ import annotations

from dataclasses import replace
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.core.integrity import IntegrityCheckResult
from generators.sales.config import load_sales_config
from generators.sales.data_dictionary import SalesDataDictionaryGenerator
from generators.sales.export import SalesCSVExporter
from generators.sales.manifest import SalesManifestGenerator
from generators.sales.manifest import _csv_metadata
from generators.sales.manifest import _gate_summary
from generators.sales.schema_sql import SalesSchemaSQLGenerator


def _dependencies_available() -> bool:
    return all(
        importlib.util.find_spec(package) is not None
        for package in ("duckdb", "numpy", "pandas", "faker")
    )


def test_sales_manifest_generator_loads_full_profile_settings() -> None:
    generator = SalesManifestGenerator.for_profile("full")

    assert generator.settings.domain == "sales"
    assert generator.settings.profile == "full"
    assert generator.settings.dataset_version == "dataset-v1.0.0"
    assert generator.settings.manifest_generated_at == "2026-09-16T00:00:00"


def test_sales_manifest_generator_refuses_non_release_profile() -> None:
    generator = SalesManifestGenerator.for_profile("dev")

    with pytest.raises(ValueError, match="requires the full profile"):
        generator.generate_manifest()


def test_sales_manifest_reports_missing_release_csvs(tmp_path: Path) -> None:
    generator = _manifest_for_output(tmp_path)

    with pytest.raises(FileNotFoundError, match="leads.csv"):
        generator.generate_manifest()


def test_sales_manifest_requires_support_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = _manifest_for_output(tmp_path)
    _write_contract_csvs(generator, tmp_path)
    monkeypatch.setattr(
        generator,
        "_validation_status",
        lambda: {"overall_passed": True, "gates": []},
    )

    with pytest.raises(FileNotFoundError, match="schema SQL"):
        generator.generate_manifest()


def test_sales_manifest_includes_complete_generation_contract(
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
            "overall_passed": True,
            "gates": [{"name": "row_caps", "passed": True, "checks": []}],
        },
    )

    manifest: dict[str, Any] = generator.generate_manifest()
    config = load_sales_config()

    assert manifest["manifest_schema_version"] == "1.0"
    assert manifest["domain"] == "sales"
    assert manifest["table_order"] == [
        "leads",
        "deals",
        "products",
        "quotations",
        "targets",
    ]
    quotations = manifest["tables"]["quotations"]
    assert quotations["role"] == "fact_junction"
    assert quotations["primary_key"] == "quotation_id"
    assert quotations["configured_rows"] == 30000
    assert quotations["fields"] == config["tables"]["quotations"]["fields"]
    assert quotations["sha256"] == manifest["hashes"]["quotations"]
    assert set(manifest["release_artifacts"]) == {
        "schema_sql",
        "data_dictionary",
    }
    assert manifest["release_artifacts"]["schema_sql"]["file"] == "schema.sql"
    assert len(manifest["release_artifacts"]["schema_sql"]["sha256"]) == 64
    assert manifest["generation_rules"] == config["generation_rules"]
    assert manifest["business_mappings"] == config["business_mappings"]
    assert manifest["relationships"] == config["relationships"]
    assert manifest["join_path_requirements"] == config["join_path_requirements"]
    assert manifest["imperfection_targets"] == config["imperfection_targets"]
    distribution = manifest["distribution_configuration"]
    assert distribution["domain_configuration_source"] == (
        "config/generation/sales.json:distributions"
    )
    assert set(distribution["settings"]) == {
        "deal_amount",
        "quotation_frequency",
        "date_clustering",
        "quota_amount",
    }
    assert distribution["settings"]["quotation_frequency"]["overrides"] == {
        "lambda": 6.0
    }


def test_sales_manifest_rejects_header_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = _manifest_for_output(tmp_path)
    _write_contract_csvs(generator, tmp_path, omit=("products", "category"))
    _write_support_artifacts(generator)
    monkeypatch.setattr(
        generator,
        "_validation_status",
        lambda: {"overall_passed": True, "gates": []},
    )

    with pytest.raises(ValueError, match="products.csv header differs"):
        generator.generate_manifest()


def test_sales_manifest_blocks_failed_validation(
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
                    "name": "join_paths",
                    "passed": False,
                    "checks": [
                        {
                            "check_name": "sales_jp_008.joined_rows",
                            "passed": False,
                            "message": "join returned no rows",
                        }
                    ],
                }
            ],
        },
    )

    with pytest.raises(ValueError, match="sales_jp_008.joined_rows"):
        generator.generate_manifest()


@pytest.mark.skipif(
    not _dependencies_available(),
    reason="duckdb, numpy, pandas, and Faker are not installed",
)
def test_full_sales_release_manifest_runs_all_gates_and_seals_atomically(
    tmp_path: Path,
) -> None:
    exporter = SalesCSVExporter.for_profile("full")
    settings = replace(exporter.settings, output_path=tmp_path)
    exporter.settings = settings
    exporter.generator.settings = settings
    exporter.export_full_profile_csvs()
    SalesSchemaSQLGenerator(exporter.generator).write_release_schema()
    SalesDataDictionaryGenerator(exporter.generator).write_release_dictionary()
    generator = SalesManifestGenerator(exporter.generator)

    manifest_path = generator.write_manifest()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest_path == tmp_path / "manifest.json"
    assert manifest["validation_status"]["overall_passed"] is True
    assert [gate["name"] for gate in manifest["validation_status"]["gates"]] == [
        "row_caps",
        "fk_integrity",
        "join_paths",
        "imperfection_rates",
    ]
    assert all(gate["passed"] for gate in manifest["validation_status"]["gates"])
    assert manifest["tables"]["quotations"]["rows"] == 30300
    assert not (tmp_path / "manifest.json.tmp").exists()
    with pytest.raises(FileExistsError, match="immutable release"):
        generator.write_manifest()


def test_sales_manifest_rejects_non_sales_settings() -> None:
    settings = GenerationSettings.from_config_files("crm", "full")

    with pytest.raises(ValueError, match="only supports sales"):
        SalesManifestGenerator(DeterministicGenerator(settings))


def test_csv_metadata_counts_sales_rows_and_columns(tmp_path: Path) -> None:
    path = tmp_path / "targets.csv"
    path.write_text(
        "target_id,rep_name,quota_amount\n1,Example Rep,100000.00\n",
        encoding="utf-8",
    )

    assert _csv_metadata(path) == {
        "columns": ["target_id", "rep_name", "quota_amount"],
        "rows": 1,
    }


def test_gate_summary_preserves_sales_check_details() -> None:
    summary = _gate_summary(
        "example",
        [IntegrityCheckResult("example.check", True, "passed")],
    )

    assert summary == {
        "name": "example",
        "passed": True,
        "checks": [
            {
                "check_name": "example.check",
                "passed": True,
                "message": "passed",
            }
        ],
    }


def _manifest_for_output(output_path: Path) -> SalesManifestGenerator:
    generator = SalesManifestGenerator.for_profile("full")
    settings = replace(generator.settings, output_path=output_path)
    generator.settings = settings
    generator.generator.settings = settings
    return generator


def _write_contract_csvs(
    generator: SalesManifestGenerator,
    output_path: Path,
    omit: tuple[str, str] | None = None,
) -> None:
    for table_name in generator.settings.table_order:
        columns = [
            field["name"]
            for field in generator.sales_config["tables"][table_name]["fields"]
            if omit != (table_name, field["name"])
        ]
        (output_path / f"{table_name}.csv").write_text(
            ",".join(columns) + "\n" + ",".join("1" for _ in columns) + "\n",
            encoding="utf-8",
        )


def _write_support_artifacts(generator: SalesManifestGenerator) -> None:
    SalesSchemaSQLGenerator(generator.generator).write_release_schema()
    SalesDataDictionaryGenerator(generator.generator).write_release_dictionary()
