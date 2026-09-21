from __future__ import annotations

import csv
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.core.integrity import IntegrityCheckResult
from generators.finance.config import load_finance_config
from generators.finance.data_dictionary import FinanceDataDictionaryGenerator
from generators.finance.export import FinanceCSVExporter
from generators.finance.manifest import FinanceManifestGenerator
from generators.finance.manifest import _csv_metadata
from generators.finance.manifest import _gate_summary
from generators.finance.schema_sql import FinanceSchemaSQLGenerator


def _dependencies_available() -> bool:
    return all(
        importlib.util.find_spec(package) is not None
        for package in ("duckdb", "numpy", "pandas", "faker")
    )


def test_finance_manifest_generator_loads_full_profile_settings() -> None:
    generator = FinanceManifestGenerator.for_profile("full")

    assert generator.settings.domain == "finance"
    assert generator.settings.profile == "full"
    assert generator.settings.dataset_version == "dataset-v1.0.0"
    assert generator.settings.manifest_generated_at == "2026-09-18T00:00:00"


def test_finance_manifest_generator_refuses_non_release_profile() -> None:
    generator = FinanceManifestGenerator.for_profile("dev")

    with pytest.raises(ValueError, match="requires the full profile"):
        generator.generate_manifest()


def test_finance_manifest_reports_missing_release_csvs(tmp_path: Path) -> None:
    generator = _manifest_for_output(tmp_path)

    with pytest.raises(FileNotFoundError, match="accounts.csv"):
        generator.generate_manifest()


def test_finance_manifest_requires_support_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = _manifest_for_output(tmp_path)
    _write_contract_csvs(generator, tmp_path)
    monkeypatch.setattr(
        generator,
        "_validation_status",
        lambda: {"overall_passed": True, "gates": [], "capabilities": {}},
    )

    with pytest.raises(FileNotFoundError, match="schema SQL"):
        generator.generate_manifest()


def test_finance_manifest_includes_complete_finance_contract(
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
            "capabilities": {
                "reproducibility": {
                    "status": "pending_step_24",
                    "passed": None,
                }
            },
        },
    )

    manifest: dict[str, Any] = generator.generate_manifest()
    config = load_finance_config()

    assert manifest["manifest_schema_version"] == "1.0"
    assert manifest["domain"] == "finance"
    assert manifest["output_path"] == "release/finance/dataset-v1.0.0"
    assert manifest["table_order"] == [
        "accounts",
        "transactions",
        "ledger_entries",
        "budgets",
        "fx_rates",
    ]
    ledger = manifest["tables"]["ledger_entries"]
    assert ledger["role"] == "fact_junction"
    assert ledger["primary_key"] == "entry_id"
    assert ledger["configured_rows"] == 200000
    assert ledger["fields"] == config["tables"]["ledger_entries"]["fields"]
    assert ledger["sha256"] == manifest["hashes"]["ledger_entries"]
    assert set(manifest["release_artifacts"]) == {
        "schema_sql",
        "data_dictionary",
        "canonical_ddl",
        "erd_dbml",
        "csv_header_spec",
    }
    assert manifest["release_artifacts"]["schema_sql"]["file"] == "schema.sql"
    assert manifest["release_artifacts"]["schema_sql"]["path"] == (
        "release/finance/dataset-v1.0.0/schema.sql"
    )
    assert len(manifest["release_artifacts"]["erd_dbml"]["sha256"]) == 64
    assert manifest["decimal_policy"] == config["decimal_policy"]
    assert manifest["accounting_rules"] == config["accounting_rules"]
    assert manifest["conversion_rules"] == config["conversion_rules"]
    assert manifest["currency_contract"]["reporting_currency"] == "USD"
    assert manifest["currency_contract"]["supported_pairs"] == config[
        "generation_rules"
    ]["fx_calendar"]["currency_pairs"]
    distribution = manifest["distribution_configuration"]
    assert set(distribution["settings"]) == {
        "transaction_amount",
        "ledger_line_frequency",
        "date_clustering",
        "budget_amount",
    }
    assert distribution["custom_algorithms"]["fx_daily_movement"]["algorithm"] == (
        "bounded_decimal_movement"
    )
    assert manifest["fx_summary"]["unique_composite_keys"] == 1
    assert manifest["fx_summary"]["transaction_rows_without_lookup"] == 0
    assert manifest["imperfection_observations"]["missing_fx_rates"]["observed"] == 0
    assert manifest["validation_status"]["capabilities"]["reproducibility"][
        "status"
    ] == "pending_step_24"


def test_finance_manifest_rejects_header_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = _manifest_for_output(tmp_path)
    _write_contract_csvs(generator, tmp_path, omit=("fx_rates", "rate_source"))
    _write_support_artifacts(generator)
    monkeypatch.setattr(
        generator,
        "_validation_status",
        lambda: {"overall_passed": True, "gates": [], "capabilities": {}},
    )

    with pytest.raises(ValueError, match="fx_rates.csv header differs"):
        generator.generate_manifest()


def test_finance_manifest_rejects_support_artifact_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = _manifest_for_output(tmp_path)
    _write_contract_csvs(generator, tmp_path)
    _write_support_artifacts(generator)
    (tmp_path / "schema.sql").write_text("SELECT 1;\n", encoding="utf-8")
    monkeypatch.setattr(
        generator,
        "_validation_status",
        lambda: {"overall_passed": True, "gates": [], "capabilities": {}},
    )

    with pytest.raises(ValueError, match="differs from canonical Finance DDL"):
        generator.generate_manifest()


def test_finance_manifest_blocks_failed_validation(
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
                    "name": "accounting",
                    "passed": False,
                    "checks": [
                        {
                            "check_name": "ledger_entries.transaction_balance",
                            "passed": False,
                            "message": "one unbalanced transaction",
                        }
                    ],
                }
            ],
            "capabilities": {},
        },
    )

    with pytest.raises(ValueError, match="ledger_entries.transaction_balance"):
        generator.generate_manifest()


@pytest.mark.skipif(
    not _dependencies_available(),
    reason="duckdb, numpy, pandas, and Faker are not installed",
)
def test_full_finance_release_manifest_runs_all_gates_and_seals_atomically(
    tmp_path: Path,
) -> None:
    exporter = FinanceCSVExporter.for_profile("full")
    settings = replace(exporter.settings, output_path=tmp_path)
    exporter.settings = settings
    exporter.generator.settings = settings
    exporter.export_full_profile_csvs()
    FinanceSchemaSQLGenerator(exporter.generator).write_release_schema()
    FinanceDataDictionaryGenerator(exporter.generator).write_release_dictionary()
    generator = FinanceManifestGenerator(exporter.generator)

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
    capabilities = manifest["validation_status"]["capabilities"]
    assert capabilities["physical_fk_integrity"] is True
    assert capabilities["accounting_integrity"] is True
    assert capabilities["fx_lookup"] is True
    assert capabilities["reproducibility"]["status"] == "pending_step_24"
    assert manifest["tables"]["budgets"]["rows"] == 808
    assert manifest["imperfection_observations"]["missing_fx_rates"] == {
        "expected": 92,
        "observed": 92,
    }
    assert manifest["imperfection_observations"]["near_duplicate_budgets"] == {
        "expected": 8,
        "observed": 8,
    }
    assert manifest["fx_summary"]["duplicate_composite_keys"] == 0
    assert manifest["fx_summary"]["transaction_rows_without_lookup"] == 0
    assert not (tmp_path / "manifest.json.tmp").exists()
    with pytest.raises(FileExistsError, match="immutable release"):
        generator.write_manifest()


def test_finance_manifest_rejects_non_finance_settings() -> None:
    settings = GenerationSettings.from_config_files("sales", "full")

    with pytest.raises(ValueError, match="only supports finance"):
        FinanceManifestGenerator(DeterministicGenerator(settings))


def test_csv_metadata_counts_finance_rows_and_columns(tmp_path: Path) -> None:
    path = tmp_path / "fx_rates.csv"
    path.write_text(
        "rate_id,from_currency,to_currency,rate\n1,USD,USD,1.000000\n",
        encoding="utf-8",
    )

    assert _csv_metadata(path) == {
        "columns": ["rate_id", "from_currency", "to_currency", "rate"],
        "rows": 1,
    }


def test_gate_summary_preserves_finance_check_details() -> None:
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


def _manifest_for_output(output_path: Path) -> FinanceManifestGenerator:
    generator = FinanceManifestGenerator.for_profile("full")
    settings = replace(generator.settings, output_path=output_path)
    generator.settings = settings
    generator.generator.settings = settings
    return generator


def _write_contract_csvs(
    generator: FinanceManifestGenerator,
    output_path: Path,
    omit: tuple[str, str] | None = None,
) -> None:
    for table_name in generator.settings.table_order:
        fields = [
            field
            for field in generator.finance_config["tables"][table_name]["fields"]
            if omit != (table_name, field["name"])
        ]
        row = [_fixture_value(table_name, field["name"], field["type"]) for field in fields]
        with (output_path / f"{table_name}.csv").open(
            "w", encoding="utf-8", newline=""
        ) as csv_file:
            writer = csv.writer(csv_file, lineterminator="\n")
            writer.writerow([field["name"] for field in fields])
            writer.writerow(row)


def _fixture_value(table_name: str, field_name: str, field_type: str) -> str:
    values = {
        "account_type": "Asset",
        "account_subtype": "Cash",
        "normal_balance": "Debit",
        "currency_code": "USD",
        "source_currency": "USD",
        "target_currency": "USD",
        "from_currency": "USD",
        "to_currency": "USD",
        "transaction_date": "2026-01-01",
        "rate_date": "2026-01-01",
        "period_start": "2026-01-01",
        "period_end": "2026-04-01",
        "created_at": "2025-12-01T00:00:00",
        "posted_at": "2026-01-01T00:00:00",
        "rate": "1.000000",
        "total_amount": "1.0000",
        "budget_amount": "1.0000",
        "debit_amount": "1.0000",
        "credit_amount": "",
        "is_active": "true",
        "reversed": "false",
        "is_estimated": "false",
    }
    if field_name == "parent_account_id":
        return ""
    if field_name in values:
        return values[field_name]
    if field_type == "integer":
        return "1"
    if field_type == "date":
        return "2026-01-01"
    if field_type == "timestamp":
        return "2026-01-01T00:00:00"
    if field_type == "boolean":
        return "false"
    return f"{table_name}_{field_name}"


def _write_support_artifacts(generator: FinanceManifestGenerator) -> None:
    FinanceSchemaSQLGenerator(generator.generator).write_release_schema()
    FinanceDataDictionaryGenerator(generator.generator).write_release_dictionary()
