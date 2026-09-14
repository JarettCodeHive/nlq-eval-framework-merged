from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from generators.core.csv_export import CSVExporter
from generators.crm.validators.fk_integrity import CRMDuckDBFKValidator
from generators.crm.validators.fk_integrity import MANUAL_FK_CHECKS
from generators.crm.validators.fk_integrity import SEMANTIC_RELATIONSHIP_CHECKS
from generators.crm.imperfections import CRMImperfectionInjector


EXPECTED_TABLE_ORDER = (
    "accounts",
    "contacts",
    "campaigns",
    "contact_campaigns",
    "interactions",
    "support_cases",
)


def _dependencies_available() -> bool:
    return all(
        importlib.util.find_spec(package) is not None
        for package in ("duckdb", "numpy", "pandas", "faker")
    )


@pytest.fixture(scope="module")
def imperfect_tables() -> dict:
    if not _dependencies_available():
        pytest.skip("duckdb, numpy, pandas, and Faker are not installed")
    return CRMImperfectionInjector.for_profile("dev").generate_imperfect_tables()


def _copy_tables(tables: dict) -> dict:
    return {name: table.copy(deep=True) for name, table in tables.items()}


def _export_tables(validator, tables: dict, output_dir: Path) -> dict[str, Path]:
    CSVExporter(validator.settings.csv_format).export_tables(
        tables,
        validator.settings.table_order,
        output_dir,
    )
    return validator._csv_paths(output_dir)


def _results_by_name(results: list) -> dict:
    return {result.check_name: result for result in results}


def test_crm_duckdb_validator_uses_engagement_schema() -> None:
    validator = CRMDuckDBFKValidator.for_profile("full")

    assert validator.settings.table_order == EXPECTED_TABLE_ORDER
    assert validator.settings.output_path.name == "dataset-v1.0.0"
    assert validator.settings.schema_source.name == "crm_ddl.sql"


def test_exported_fk_validation_reports_missing_engagement_csvs(
    tmp_path: Path,
) -> None:
    validator = CRMDuckDBFKValidator.for_profile("dev")
    for table_name in EXPECTED_TABLE_ORDER:
        if table_name != "contact_campaigns":
            (tmp_path / f"{table_name}.csv").write_text("\n", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="contact_campaigns.csv"):
        validator._csv_paths(tmp_path)


def test_manual_fk_checks_cover_every_engagement_relationship() -> None:
    assert set(MANUAL_FK_CHECKS) == {
        "contacts.account_id.fk",
        "contact_campaigns.contact_id.fk",
        "contact_campaigns.campaign_id.fk",
        "interactions.contact_id.fk",
        "interactions.account_id.fk",
        "interactions.campaign_id.fk",
        "support_cases.account_id.fk",
        "support_cases.contact_id.fk",
    }
    assert "PRAGMA foreign_key_check" not in "".join(MANUAL_FK_CHECKS.values())


def test_semantic_checks_cover_cross_column_relationships() -> None:
    assert set(SEMANTIC_RELATIONSHIP_CHECKS) == {
        "contact_campaigns.composite_pk",
        "interactions.contact_account_consistency",
        "interactions.contact_campaign_membership",
        "support_cases.contact_account_consistency",
    }


def test_generated_fk_validation_passes_all_checks() -> None:
    if not _dependencies_available():
        pytest.skip("duckdb, numpy, pandas, and Faker are not installed")

    results = CRMDuckDBFKValidator.for_profile("dev").generate_and_validate()
    checks = _results_by_name(results)

    assert all(result.passed for result in results)
    assert len(results) == 19
    assert checks["schema.duckdb_ddl"].passed
    for table_name in EXPECTED_TABLE_ORDER:
        assert checks[f"{table_name}.duckdb_load"].passed


def test_constrained_load_rejects_invalid_nullable_fk(
    imperfect_tables: dict,
    tmp_path: Path,
) -> None:
    validator = CRMDuckDBFKValidator.for_profile("dev")
    malformed = _copy_tables(imperfect_tables)
    position = malformed["contacts"].index[
        malformed["contacts"]["account_id"].astype(str).ne("")
    ][0]
    malformed["contacts"].loc[position, "account_id"] = 999999

    results = validator._validate_csv_paths(
        _export_tables(validator, malformed, tmp_path)
    )
    checks = _results_by_name(results)

    assert checks["schema.duckdb_ddl"].passed
    assert not checks["contacts.duckdb_load"].passed


def test_constrained_load_rejects_duplicate_composite_key(
    imperfect_tables: dict,
    tmp_path: Path,
) -> None:
    validator = CRMDuckDBFKValidator.for_profile("dev")
    malformed = _copy_tables(imperfect_tables)
    malformed["contact_campaigns"].loc[1, ["contact_id", "campaign_id"]] = malformed[
        "contact_campaigns"
    ].loc[0, ["contact_id", "campaign_id"]]

    results = validator._validate_csv_paths(
        _export_tables(validator, malformed, tmp_path)
    )
    checks = _results_by_name(results)

    assert checks["contacts.duckdb_load"].passed
    assert checks["campaigns.duckdb_load"].passed
    assert not checks["contact_campaigns.duckdb_load"].passed


def test_semantic_checks_detect_valid_fk_but_inconsistent_paths(
    imperfect_tables: dict,
    tmp_path: Path,
) -> None:
    validator = CRMDuckDBFKValidator.for_profile("dev")
    malformed = _copy_tables(imperfect_tables)
    interactions = malformed["interactions"]
    interaction_position = interactions.index[
        interactions["campaign_id"].astype(str).ne("")
    ][0]
    contact_id = int(interactions.loc[interaction_position, "contact_id"])
    contact_account = (
        malformed["contacts"]
        .loc[
            malformed["contacts"]["contact_id"] == contact_id,
            "account_id",
        ]
        .iloc[0]
    )
    replacement_account = next(
        account_id
        for account_id in malformed["accounts"]["account_id"]
        if str(account_id) != str(contact_account)
    )
    member_campaigns = set(
        malformed["contact_campaigns"].loc[
            malformed["contact_campaigns"]["contact_id"] == contact_id,
            "campaign_id",
        ]
    )
    replacement_campaign = next(
        campaign_id
        for campaign_id in malformed["campaigns"]["campaign_id"]
        if campaign_id not in member_campaigns
    )
    interactions.loc[interaction_position, "account_id"] = replacement_account
    interactions.loc[interaction_position, "campaign_id"] = replacement_campaign

    support_cases = malformed["support_cases"]
    case_position = support_cases.index[support_cases["contact_id"].astype(str).ne("")][
        0
    ]
    case_account = support_cases.loc[case_position, "account_id"]
    replacement_contact = (
        malformed["contacts"]
        .loc[
            malformed["contacts"]["account_id"].astype(str).ne(str(case_account))
            & malformed["contacts"]["account_id"].astype(str).ne(""),
            "contact_id",
        ]
        .iloc[0]
    )
    support_cases.loc[case_position, "contact_id"] = replacement_contact

    results = validator._validate_csv_paths(
        _export_tables(validator, malformed, tmp_path)
    )
    checks = _results_by_name(results)

    assert all(checks[f"{table}.duckdb_load"].passed for table in EXPECTED_TABLE_ORDER)
    assert not checks["interactions.contact_account_consistency"].passed
    assert not checks["interactions.contact_campaign_membership"].passed
    assert not checks["support_cases.contact_account_consistency"].passed
