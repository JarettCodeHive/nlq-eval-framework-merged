from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from generators.core.integrity import IntegrityCheckResult
from generators.crm.config import load_crm_config
from generators.crm.manifest import CRMManifestGenerator
from generators.crm.manifest import _csv_metadata
from generators.crm.manifest import _gate_summary


def test_crm_manifest_generator_loads_full_profile_settings() -> None:
    generator = CRMManifestGenerator.for_profile("full")

    assert generator.settings.domain == "crm"
    assert generator.settings.profile == "full"
    assert generator.settings.dataset_version == "dataset-v1.0.0"
    assert generator.settings.manifest_generated_at == "2026-09-04T00:00:00"


def test_crm_manifest_generator_refuses_non_release_profile() -> None:
    generator = CRMManifestGenerator.for_profile("dev")

    with pytest.raises(ValueError, match="full profile"):
        generator.generate_manifest()


def test_crm_manifest_generator_reports_missing_release_csvs(tmp_path: Path) -> None:
    generator = CRMManifestGenerator.for_profile("full")
    generator.settings = replace(generator.settings, output_path=tmp_path)
    generator.generator.settings = generator.settings

    with pytest.raises(FileNotFoundError, match="export-csvs"):
        generator.generate_manifest()


def test_crm_manifest_includes_engagement_schema_and_generation_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = CRMManifestGenerator.for_profile("full")
    generator.settings = replace(generator.settings, output_path=tmp_path)
    generator.generator.settings = generator.settings
    crm_config = load_crm_config()
    columns_by_table = {
        table_name: [
            field["name"] for field in crm_config["tables"][table_name]["fields"]
        ]
        for table_name in generator.settings.table_order
    }
    for table_name in generator.settings.table_order:
        columns = columns_by_table[table_name]
        (tmp_path / f"{table_name}.csv").write_text(
            ",".join(columns) + "\n" + ",".join("1" for _ in columns) + "\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(
        generator,
        "_validation_status",
        lambda: {
            "overall_passed": True,
            "gates": [
                {
                    "name": "row_caps",
                    "passed": True,
                    "checks": [],
                }
            ],
        },
    )
    monkeypatch.setattr(
        "generators.crm.manifest._relative",
        lambda path: str(path),
    )

    manifest: dict[str, Any] = generator.generate_manifest()

    assert manifest["table_order"] == [
        "accounts",
        "contacts",
        "campaigns",
        "contact_campaigns",
        "interactions",
        "support_cases",
    ]
    bridge = manifest["tables"]["contact_campaigns"]
    assert bridge["file"] == "contact_campaigns.csv"
    assert bridge["role"] == "junction"
    assert bridge["primary_key"] == ["contact_id", "campaign_id"]
    assert bridge["configured_rows"] == 180000
    assert bridge["rows"] == 1
    assert bridge["columns"] == columns_by_table["contact_campaigns"]
    assert bridge["fields"] == crm_config["tables"]["contact_campaigns"]["fields"]
    assert (
        manifest["hashes"]["contact_campaigns"]
        == manifest["tables"]["contact_campaigns"]["sha256"]
    )
    assert manifest["distribution_targets"] == crm_config["distribution_targets"]
    distribution_configuration = manifest["distribution_configuration"]
    assert distribution_configuration["shared_defaults_source"] == (
        "config/generation/base.json:distribution_defaults"
    )
    assert distribution_configuration["domain_configuration_source"] == (
        "config/generation/crm.json:distributions"
    )
    campaign_budget = distribution_configuration["settings"]["campaign_budget"]
    assert campaign_budget["preset"] == "pareto_amount"
    assert campaign_budget["overrides"] == {}
    assert (
        campaign_budget["effective_parameters"]
        == manifest["distributions"]["campaign_budget"]
    )
    assert campaign_budget["targets"] == {
        "campaign_budget": crm_config["distribution_targets"]["campaign_budget"]
    }
    assert set(distribution_configuration["settings"]) == {
        "campaign_budget",
        "interaction_frequency",
        "date_clustering",
    }
    assert distribution_configuration["settings"]["date_clustering"]["targets"] == {
        "date_clustering": crm_config["distribution_targets"]["date_clustering"]
    }
    assert manifest["imperfection_targets"] == crm_config["imperfection_targets"]
    assert manifest["relationships"] == crm_config["relationships"]
    assert manifest["generation_rules"] == crm_config["generation_rules"]
    assert manifest["business_mappings"] == crm_config["business_mappings"]


def test_crm_manifest_rejects_csv_header_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = CRMManifestGenerator.for_profile("full")
    generator.settings = replace(generator.settings, output_path=tmp_path)
    generator.generator.settings = generator.settings
    crm_config = load_crm_config()
    for table_name in generator.settings.table_order:
        columns = [
            field["name"] for field in crm_config["tables"][table_name]["fields"]
        ]
        if table_name == "campaigns":
            columns.remove("campaign_type")
        (tmp_path / f"{table_name}.csv").write_text(
            ",".join(columns) + "\n" + ",".join("1" for _ in columns) + "\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(
        generator,
        "_validation_status",
        lambda: {"overall_passed": True, "gates": []},
    )
    monkeypatch.setattr(
        "generators.crm.manifest._relative",
        lambda path: str(path),
    )

    with pytest.raises(ValueError, match="campaigns.csv header differs"):
        generator.generate_manifest()


def test_csv_metadata_counts_rows_and_columns(tmp_path: Path) -> None:
    csv_path = tmp_path / "accounts.csv"
    csv_path.write_text(
        (
            "account_id,account_name,account_size,industry,region,"
            "customer_tier,is_active,created_at\n"
            "1,Example,250,Technology,North,Strategic,true,2026-01-01T00:00:00\n"
            "2,Other,75,Finance,West,Standard,true,2026-01-02T00:00:00\n"
        ),
        encoding="utf-8",
    )

    metadata = _csv_metadata(csv_path)

    assert metadata == {
        "columns": [
            "account_id",
            "account_name",
            "account_size",
            "industry",
            "region",
            "customer_tier",
            "is_active",
            "created_at",
        ],
        "rows": 2,
    }


def test_gate_summary_preserves_check_details() -> None:
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
