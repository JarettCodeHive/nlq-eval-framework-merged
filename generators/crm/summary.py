"""Human-readable CRM generation summaries for the root CLI."""

from __future__ import annotations

from typing import Any

from generators.crm.imperfections import CRMImperfectionInjector


def print_relationship_summary(tables: dict[str, Any]) -> None:
    """Print CRM relationship and semantic-link coverage."""

    accounts = tables["accounts"]
    contacts = tables["contacts"]
    campaigns = tables["campaigns"]
    contact_campaigns = tables["contact_campaigns"]
    interactions = tables["interactions"]
    support_cases = tables["support_cases"]

    account_ids = set(accounts["account_id"].tolist())
    contact_ids = set(contacts["contact_id"].tolist())
    campaign_ids = set(campaigns["campaign_id"].tolist())
    membership_pairs = set(
        zip(
            contact_campaigns["contact_id"],
            contact_campaigns["campaign_id"],
            strict=True,
        )
    )
    attributed_interactions = interactions[interactions["campaign_id"] != ""]
    contact_accounts = dict(
        zip(contacts["contact_id"], contacts["account_id"], strict=True)
    )
    contacts_with_multiple_campaigns = int(
        contact_campaigns.groupby("contact_id")["campaign_id"].nunique().ge(2).sum()
    )
    campaigns_with_multiple_contacts = int(
        contact_campaigns.groupby("campaign_id")["contact_id"].nunique().ge(2).sum()
    )
    interaction_accounts_consistent = all(
        row.account_id == "" or row.account_id == contact_accounts[row.contact_id]
        for row in interactions.itertuples(index=False)
    )
    case_contacts_consistent = all(
        row.contact_id == "" or contact_accounts[row.contact_id] == row.account_id
        for row in support_cases.itertuples(index=False)
    )

    print("relationship_checks:")
    print(
        "  contacts.account_id valid: "
        f"{_non_empty_values(contacts['account_id']).issubset(account_ids)}"
    )
    print(
        "  contact_campaigns.contact_id valid: "
        f"{set(contact_campaigns['contact_id']).issubset(contact_ids)}"
    )
    print(
        "  contact_campaigns.campaign_id valid: "
        f"{set(contact_campaigns['campaign_id']).issubset(campaign_ids)}"
    )
    print(f"  contacts_with_multiple_campaigns: {contacts_with_multiple_campaigns}")
    print(f"  campaigns_with_multiple_contacts: {campaigns_with_multiple_contacts}")
    print(
        "  interactions.contact_id valid: "
        f"{set(interactions['contact_id']).issubset(contact_ids)}"
    )
    print(
        "  interactions.campaign_id valid: "
        f"{_non_empty_values(interactions['campaign_id']).issubset(campaign_ids)}"
    )
    print(
        "  interactions.account_id valid: "
        f"{_non_empty_values(interactions['account_id']).issubset(account_ids)}"
    )
    print(
        f"  interaction contact/account consistent: {interaction_accounts_consistent}"
    )
    memberships_valid = all(
        (row.contact_id, row.campaign_id) in membership_pairs
        for row in attributed_interactions.itertuples(index=False)
    )
    print(f"  attributed interactions have membership: {memberships_valid}")
    print(
        "  support_cases.account_id valid: "
        f"{set(support_cases['account_id']).issubset(account_ids)}"
    )
    print(
        "  support_cases.contact_id valid: "
        f"{_non_empty_values(support_cases['contact_id']).issubset(contact_ids)}"
    )
    print(f"  support-case contact/account consistent: {case_contacts_consistent}")


def print_distribution_summary(tables: dict[str, Any]) -> None:
    """Print concise CRM distribution measurements."""

    campaigns = tables["campaigns"]
    interactions = tables["interactions"]
    support_cases = tables["support_cases"]
    campaign_budgets = campaigns.loc[
        campaigns["budget_amount"].astype(str).ne(""), "budget_amount"
    ].astype(float)
    engagement_points = interactions["engagement_points"].astype(int)
    unresolved_cases = support_cases[support_cases["resolved_at"].astype(str).eq("")]
    resolved_cases = support_cases[support_cases["resolved_at"].astype(str).ne("")]
    sla_met = int(
        resolved_cases["resolved_at"]
        .astype(str)
        .le(resolved_cases["sla_due_at"].astype(str))
        .sum()
    )

    print("distribution_checks:")
    print(f"  campaign_budget_min: {campaign_budgets.min():.2f}")
    print(f"  campaign_budget_max: {campaign_budgets.max():.2f}")
    print(f"  campaign_budget_mean: {campaign_budgets.mean():.2f}")
    print(f"  interaction_contacts: {interactions['contact_id'].nunique()}")
    print(f"  engagement_points_min: {engagement_points.min()}")
    print(f"  engagement_points_max: {engagement_points.max()}")
    print(f"  engagement_points_mean: {engagement_points.mean():.2f}")
    attributed_campaigns = interactions.loc[
        interactions["campaign_id"].astype(str).ne(""), "campaign_id"
    ].nunique()
    print(f"  attributed_interaction_campaigns: {attributed_campaigns}")
    print(f"  organic_interactions: {_empty_count(interactions['campaign_id'])}")
    print(f"  support_case_open_dates: {support_cases['opened_at'].nunique()}")
    print(f"  support_cases_total: {len(support_cases)}")
    print(f"  support_cases_unresolved: {len(unresolved_cases)}")
    print(f"  resolved_cases_within_sla: {sla_met}/{len(resolved_cases)}")


def print_imperfection_summary(
    tables: dict[str, Any],
    injector: CRMImperfectionInjector,
) -> None:
    """Print concise CRM imperfection measurements."""

    contacts = tables["contacts"]
    contact_campaigns = tables["contact_campaigns"]
    interactions = tables["interactions"]
    support_cases = tables["support_cases"]
    duplicate_rows = len(contacts) - injector.generator.row_count("contacts")
    outlier_target = injector.crm_config["imperfection_targets"][
        "engagement_point_outliers"
    ]
    outlier_minimum = int(outlier_target["minimum_value"])
    expected_boundaries = {
        f"{value}T00:00:00" for value in injector.config["boundary_dates"]
    }

    print("imperfection_checks:")
    print(f"  contact_near_duplicates: {duplicate_rows}")
    print(
        "  contact_campaigns.attribution_weight_empty: "
        f"{_empty_count(contact_campaigns['attribution_weight'])}"
    )
    outliers = int(
        (interactions["engagement_points"].astype(int) >= outlier_minimum).sum()
    )
    print(f"  engagement_point_outliers_ge_{outlier_minimum}: {outliers}")
    boundary_count = int(
        support_cases["opened_at"].astype(str).isin(expected_boundaries).sum()
    )
    print(f"  support_case_boundary_openings: {boundary_count}")


def _empty_count(values: Any) -> int:
    return int((values.astype(str) == "").sum())


def _non_empty_values(values: Any) -> set[Any]:
    return {value for value in values.tolist() if str(value) != ""}


__all__ = [
    "print_distribution_summary",
    "print_imperfection_summary",
    "print_relationship_summary",
]
