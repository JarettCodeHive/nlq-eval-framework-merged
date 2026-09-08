from __future__ import annotations

import importlib.util

import pytest

from generators.crm.config import load_crm_config
from generators.crm.generator import CRM_COLUMN_CONTRACTS
from generators.crm.imperfections import CRMImperfectionInjector
from generators.crm.integrity import CRMRelationalValidator


def _dependencies_available() -> bool:
    return all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    )


@pytest.fixture(scope="module")
def imperfect_tables() -> dict:
    if not _dependencies_available():
        pytest.skip("numpy, pandas, and Faker are not installed")
    return CRMImperfectionInjector.for_profile("dev").generate_imperfect_tables()


def _copy_tables(tables: dict) -> dict:
    return {name: table.copy(deep=True) for name, table in tables.items()}


def _results_by_name(results: list) -> dict:
    return {result.check_name: result for result in results}


def test_crm_relational_validator_uses_engagement_contract() -> None:
    validator = CRMRelationalValidator.for_profile("dev")

    assert validator.settings.table_order == tuple(CRM_COLUMN_CONTRACTS)
    assert validator.crm_config["tables"]["contact_campaigns"]["primary_key"] == [
        "contact_id",
        "campaign_id",
    ]
    assert "opportunities" not in validator.crm_config["tables"]
    assert "activities" not in validator.crm_config["tables"]


def test_generated_relational_validation_passes() -> None:
    if not _dependencies_available():
        pytest.skip("numpy, pandas, and Faker are not installed")

    results = CRMRelationalValidator.for_profile("dev").generate_and_validate()
    checks = _results_by_name(results)

    assert results
    assert all(result.passed for result in results)
    assert "contact_campaigns.contact_many_campaigns" in checks
    assert "contact_campaigns.campaign_many_contacts" in checks
    assert "interactions.contact_campaign_membership" in checks
    assert "support_cases.contact_account_consistency" in checks
    assert "campaigns.status_end_date" in checks
    assert "support_cases.status_resolved_at" in checks
    assert "campaigns.currency_code" in checks


def test_missing_table_reports_contract_failure_without_crashing(
    imperfect_tables: dict,
) -> None:
    malformed = _copy_tables(imperfect_tables)
    malformed.pop("support_cases")

    results = CRMRelationalValidator.for_profile("dev").validate_tables(malformed)
    checks = _results_by_name(results)

    assert not checks["schema.table_order"].passed
    assert not checks["support_cases.present"].passed


def test_wrong_column_order_stops_dependent_checks(imperfect_tables: dict) -> None:
    malformed = _copy_tables(imperfect_tables)
    columns = malformed["campaigns"].columns.tolist()
    columns[0], columns[1] = columns[1], columns[0]
    malformed["campaigns"] = malformed["campaigns"][columns]

    results = CRMRelationalValidator.for_profile("dev").validate_tables(malformed)
    checks = _results_by_name(results)

    assert not checks["campaigns.columns"].passed
    assert "campaigns.row_count" not in checks


def test_key_fk_and_required_field_failures_are_reported(
    imperfect_tables: dict,
) -> None:
    malformed = _copy_tables(imperfect_tables)
    malformed["contact_campaigns"].loc[1, ["contact_id", "campaign_id"]] = malformed[
        "contact_campaigns"
    ].loc[0, ["contact_id", "campaign_id"]]
    malformed["contacts"].loc[0, "first_name"] = ""
    malformed["support_cases"].loc[0, "account_id"] = 999999

    checks = _results_by_name(
        CRMRelationalValidator.for_profile("dev").validate_tables(malformed)
    )

    assert not checks["contact_campaigns.primary_key"].passed
    assert not checks["contact_campaigns.composite_key"].passed
    assert not checks["contacts.first_name.not_null"].passed
    assert not checks["support_cases.account_id.fk"].passed


def test_primary_attribution_and_weight_sum_failures_are_reported(
    imperfect_tables: dict,
) -> None:
    malformed = _copy_tables(imperfect_tables)
    memberships = malformed["contact_campaigns"]
    contact_id = (
        memberships.groupby("contact_id").size().loc[lambda value: value >= 2].index[0]
    )
    positions = memberships.index[memberships["contact_id"] == contact_id][:2]
    memberships.loc[positions, "is_primary_attribution"] = True
    memberships.loc[positions[0], "attribution_weight"] = "0.9999"

    checks = _results_by_name(
        CRMRelationalValidator.for_profile("dev").validate_tables(malformed)
    )

    assert not checks["contact_campaigns.primary_per_contact"].passed
    assert not checks["contact_campaigns.attribution_sums"].passed


def test_interaction_consistency_and_membership_failures_are_reported(
    imperfect_tables: dict,
) -> None:
    malformed = _copy_tables(imperfect_tables)
    interactions = malformed["interactions"]
    position = interactions.index[interactions["campaign_id"].astype(str).ne("")][0]
    contact_id = int(interactions.loc[position, "contact_id"])
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
    interactions.loc[position, "campaign_id"] = replacement_campaign
    interactions.loc[position, "account_id"] = 999999

    checks = _results_by_name(
        CRMRelationalValidator.for_profile("dev").validate_tables(malformed)
    )

    assert not checks["interactions.account_id.fk"].passed
    assert not checks["interactions.contact_account_consistency"].passed
    assert not checks["interactions.contact_campaign_membership"].passed


def test_support_temporal_status_and_account_failures_are_reported(
    imperfect_tables: dict,
) -> None:
    malformed = _copy_tables(imperfect_tables)
    support_cases = malformed["support_cases"]
    resolved_position = support_cases.index[
        support_cases["status"].isin({"Resolved", "Closed"})
    ][0]
    support_cases.loc[resolved_position, "resolved_at"] = ""
    support_cases.loc[resolved_position, "sla_due_at"] = support_cases.loc[
        resolved_position, "opened_at"
    ]
    support_cases.loc[resolved_position, "contact_id"] = (
        malformed["contacts"]
        .loc[
            malformed["contacts"]["account_id"]
            != support_cases.loc[resolved_position, "account_id"],
            "contact_id",
        ]
        .iloc[0]
    )

    checks = _results_by_name(
        CRMRelationalValidator.for_profile("dev").validate_tables(malformed)
    )

    assert not checks["support_cases.contact_account_consistency"].passed
    assert not checks["support_cases.temporal"].passed
    assert not checks["support_cases.status_resolved_at"].passed


def test_campaign_temporal_status_and_currency_failures_are_reported(
    imperfect_tables: dict,
) -> None:
    malformed = _copy_tables(imperfect_tables)
    campaigns = malformed["campaigns"]
    completed_position = campaigns.index[
        campaigns["status"].isin(
            set(
                load_crm_config()["business_mappings"]["campaign_status_rules"][
                    "requires_end_date"
                ]
            )
        )
    ][0]
    campaigns.loc[completed_position, "end_date"] = ""
    campaigns.loc[completed_position, "created_at"] = "2099-12-31T00:00:00"
    campaigns.loc[completed_position, "currency_code"] = "EUR"

    checks = _results_by_name(
        CRMRelationalValidator.for_profile("dev").validate_tables(malformed)
    )

    assert not checks["campaigns.temporal"].passed
    assert not checks["campaigns.status_end_date"].passed
    assert not checks["campaigns.currency_code"].passed
