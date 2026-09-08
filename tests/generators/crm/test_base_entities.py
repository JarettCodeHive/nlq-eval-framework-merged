from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
import importlib.util

import pytest

from generators.common.base import DeterministicGenerator
from generators.crm.config import load_crm_config
from generators.crm.config import settings_for_profile
from generators.crm.generator import CRMBaseEntityGenerator
from generators.crm.generator import CRM_COLUMN_CONTRACTS


EXPECTED_COLUMNS = CRM_COLUMN_CONTRACTS


def _dependencies_available() -> bool:
    return all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    )


pytestmark = pytest.mark.skipif(
    not _dependencies_available(),
    reason="numpy, pandas, and Faker are not installed",
)


def test_crm_column_order_matches_engagement_schema() -> None:
    assert EXPECTED_COLUMNS == {
        "accounts": [
            "account_id",
            "account_name",
            "account_size",
            "industry",
            "region",
            "customer_tier",
            "is_active",
            "created_at",
        ],
        "contacts": [
            "contact_id",
            "account_id",
            "first_name",
            "last_name",
            "email",
            "address",
            "title",
            "is_active",
            "created_at",
        ],
        "campaigns": [
            "campaign_id",
            "campaign_name",
            "campaign_type",
            "primary_channel",
            "status",
            "start_date",
            "end_date",
            "budget_amount",
            "currency_code",
            "created_at",
        ],
        "contact_campaigns": [
            "contact_id",
            "campaign_id",
            "member_status",
            "first_touch_at",
            "last_touch_at",
            "attribution_weight",
            "is_primary_attribution",
            "created_at",
        ],
        "interactions": [
            "interaction_id",
            "contact_id",
            "account_id",
            "campaign_id",
            "engagement_type",
            "channel",
            "direction",
            "engagement_points",
            "interaction_at",
            "created_at",
        ],
        "support_cases": [
            "case_id",
            "account_id",
            "contact_id",
            "case_number",
            "subject",
            "category",
            "priority",
            "status",
            "opened_at",
            "sla_due_at",
            "resolved_at",
            "created_at",
        ],
    }


def test_generate_dev_base_tables() -> None:
    generator = CRMBaseEntityGenerator.for_profile("dev")
    tables = generator.generate_tables()
    rules = generator.crm_config["generation_rules"]
    mappings = generator.crm_config["business_mappings"]

    assert list(tables) == list(EXPECTED_COLUMNS)
    for table_name, columns in EXPECTED_COLUMNS.items():
        assert tables[table_name].columns.tolist() == columns
        assert len(tables[table_name]) == generator.generator.row_count(table_name)

    account_size = rules["accounts"]["account_size"]
    assert (
        tables["accounts"]["account_size"]
        .between(account_size["minimum"], account_size["maximum"])
        .all()
    )
    assert tables["accounts"]["account_name"].str.len().gt(0).all()
    assert (
        not tables["accounts"]["account_name"]
        .str.contains(r"[^A-Za-z0-9 ]", regex=True)
        .any()
    )
    assert (tables["contacts"]["account_id"] == "").any()
    assert tables["contacts"]["email"].str.endswith("@example.test").all()
    assert set(tables["campaigns"]["currency_code"]) == {mappings["currency_code"]}
    assert (tables["campaigns"]["budget_amount"] == "").any()


def test_base_relationships_and_left_join_coverage() -> None:
    tables = CRMBaseEntityGenerator.for_profile("dev").generate_tables()
    accounts = tables["accounts"]
    contacts = tables["contacts"]
    campaigns = tables["campaigns"]
    memberships = tables["contact_campaigns"]
    interactions = tables["interactions"]
    support_cases = tables["support_cases"]

    assert set(contacts.loc[contacts["account_id"] != "", "account_id"]).issubset(
        set(accounts["account_id"])
    )
    assert memberships.duplicated(["contact_id", "campaign_id"]).sum() == 0
    assert set(memberships["contact_id"]).issubset(set(contacts["contact_id"]))
    assert set(memberships["campaign_id"]).issubset(set(campaigns["campaign_id"]))
    assert memberships.groupby("contact_id")["campaign_id"].nunique().max() >= 2
    assert memberships.groupby("campaign_id")["contact_id"].nunique().max() >= 2

    primary_counts = (
        memberships[memberships["is_primary_attribution"]].groupby("contact_id").size()
    )
    assert primary_counts.max() <= 1
    attribution_sums = memberships.groupby("contact_id")["attribution_weight"].apply(
        lambda values: sum(Decimal(value) for value in values)
    )
    expected_sum = Decimal(
        load_crm_config()["business_mappings"]["attribution"][
            "expected_sum_before_imperfections"
        ]
    )
    assert (attribution_sums == expected_sum).all()

    membership_pairs = set(
        zip(memberships["contact_id"], memberships["campaign_id"], strict=True)
    )
    attributed = interactions[interactions["campaign_id"] != ""]
    assert all(
        (row.contact_id, row.campaign_id) in membership_pairs
        for row in attributed.itertuples(index=False)
    )
    assert (interactions["campaign_id"] == "").any()
    assert (interactions["account_id"] == "").any()
    assert (support_cases["contact_id"] == "").any()

    accounts_with_contacts = set(
        contacts.loc[contacts["account_id"] != "", "account_id"]
    )
    assert set(accounts["account_id"]) - accounts_with_contacts
    assert set(contacts["contact_id"]) - set(interactions["contact_id"])
    assert set(contacts["contact_id"]) - set(
        support_cases.loc[support_cases["contact_id"] != "", "contact_id"]
    )
    assert set(campaigns["campaign_id"]) - set(attributed["campaign_id"])


def test_base_business_and_timestamp_rules() -> None:
    generator = CRMBaseEntityGenerator.for_profile("dev")
    tables = generator.generate_tables()
    mappings = generator.crm_config["business_mappings"]

    for row in tables["campaigns"].itertuples(index=False):
        if row.end_date != "":
            assert row.end_date >= row.start_date
    for row in tables["contact_campaigns"].itertuples(index=False):
        assert datetime.fromisoformat(row.last_touch_at) >= datetime.fromisoformat(
            row.first_touch_at
        )
    for row in tables["interactions"].itertuples(index=False):
        assert row.channel == mappings["engagement_channels"][row.engagement_type]
        assert (
            row.engagement_points == mappings["engagement_points"][row.engagement_type]
        )
        assert datetime.fromisoformat(row.created_at) >= datetime.fromisoformat(
            row.interaction_at
        )

    resolved_statuses = set(mappings["resolved_case_statuses"])
    contact_accounts = dict(
        zip(
            tables["contacts"]["contact_id"],
            tables["contacts"]["account_id"],
            strict=True,
        )
    )
    for row in tables["support_cases"].itertuples(index=False):
        opened_at = datetime.fromisoformat(row.opened_at)
        sla_due_at = datetime.fromisoformat(row.sla_due_at)
        assert sla_due_at >= opened_at
        assert (row.resolved_at != "") == (row.status in resolved_statuses)
        if row.resolved_at != "":
            assert datetime.fromisoformat(row.resolved_at) >= opened_at
        if row.contact_id != "":
            assert contact_accounts[row.contact_id] == row.account_id


def test_base_generation_covers_every_enum_and_conditional_field_rule() -> None:
    generator = CRMBaseEntityGenerator.for_profile("dev")
    tables = generator.generate_tables()
    values = generator.crm_config["domain_values"]
    enum_columns = {
        "industries": ("accounts", "industry"),
        "regions": ("accounts", "region"),
        "customer_tiers": ("accounts", "customer_tier"),
        "contact_titles": ("contacts", "title"),
        "campaign_types": ("campaigns", "campaign_type"),
        "campaign_channels": ("campaigns", "primary_channel"),
        "campaign_statuses": ("campaigns", "status"),
        "campaign_member_statuses": ("contact_campaigns", "member_status"),
        "engagement_types": ("interactions", "engagement_type"),
        "interaction_directions": ("interactions", "direction"),
        "support_case_categories": ("support_cases", "category"),
        "support_case_priorities": ("support_cases", "priority"),
        "support_case_statuses": ("support_cases", "status"),
    }
    for value_name, (table_name, column_name) in enum_columns.items():
        assert set(tables[table_name][column_name]) == set(values[value_name])

    campaigns = tables["campaigns"]
    status_rules = generator.crm_config["business_mappings"]["campaign_status_rules"]
    requires_end_date = set(status_rules["requires_end_date"])
    open_ended = set(values["campaign_statuses"]) - requires_end_date
    assert (
        campaigns.loc[
            campaigns["status"].isin(requires_end_date),
            "end_date",
        ]
        .ne("")
        .all()
    )
    assert (
        campaigns.loc[
            campaigns["status"].isin(open_ended),
            "end_date",
        ]
        .eq("")
        .all()
    )

    mappings = load_crm_config()["business_mappings"]
    support_cases = tables["support_cases"]
    assert (
        support_cases.loc[
            support_cases["status"].isin(mappings["resolved_case_statuses"]),
            "resolved_at",
        ]
        .ne("")
        .all()
    )
    assert (
        support_cases.loc[
            support_cases["status"].isin(mappings["unresolved_case_statuses"]),
            "resolved_at",
        ]
        .eq("")
        .all()
    )


@pytest.mark.parametrize("profile", ["dev", "full"])
def test_base_generation_is_reproducible_for_both_profiles(profile: str) -> None:
    settings = settings_for_profile(profile)
    if profile == "full":
        settings = replace(
            settings,
            row_counts=settings_for_profile("dev").row_counts,
        )

    first = CRMBaseEntityGenerator(DeterministicGenerator(settings)).generate_tables()
    second = CRMBaseEntityGenerator(DeterministicGenerator(settings)).generate_tables()

    pd = pytest.importorskip("pandas")
    for table_name in EXPECTED_COLUMNS:
        pd.testing.assert_frame_equal(first[table_name], second[table_name])
