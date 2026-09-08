from __future__ import annotations

from datetime import datetime
from datetime import timedelta
import importlib.util

import pytest

from generators.common.imperfections import count_from_pct
from generators.crm.distributions import CRMDistributionApplier
from generators.crm.generator import CRM_COLUMN_CONTRACTS
from generators.crm.imperfections import CRMImperfectionInjector
from generators.crm.imperfections import _near_duplicate_email
from generators.crm.imperfections import _near_duplicate_first_name


def _dependencies_available() -> bool:
    return all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    )


@pytest.fixture(scope="module")
def distributed_and_imperfect_tables() -> tuple[dict, dict]:
    if not _dependencies_available():
        pytest.skip("numpy, pandas, and Faker are not installed")
    distributed = CRMDistributionApplier.for_profile(
        "dev"
    ).generate_distributed_tables()
    snapshot = {
        table_name: table.copy(deep=True) for table_name, table in distributed.items()
    }
    imperfect = CRMImperfectionInjector.for_profile("dev").apply_to_tables(distributed)
    for table_name in distributed:
        assert distributed[table_name].equals(snapshot[table_name])
    return distributed, imperfect


def test_crm_imperfection_settings_and_targets_are_loaded() -> None:
    injector = CRMImperfectionInjector.for_profile("dev")
    target = injector.crm_config["imperfection_targets"]["engagement_point_outliers"]

    assert injector.settings.imperfections["duplicate_pct"] == 1.0
    assert injector.settings.imperfections["null_pct"] == 2.5
    assert injector.settings.imperfections["outlier_pct"] == 0.5
    assert "2038-01-19" in injector.settings.imperfections["boundary_dates"]
    assert target["minimum_value"] == 50
    assert target["maximum_value"] == 100


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        ("John", "Jonh"),
        ("Emma", "Emam"),
        ("Lee", "Ele"),
        ("A", "AA"),
        ("", "Duplicate"),
    ],
)
def test_near_duplicate_first_name_uses_typographical_variation(
    original: str,
    expected: str,
) -> None:
    assert _near_duplicate_first_name(original) == expected


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        ("john.smith@example.test", "john.smiht@example.test"),
        ("john@example.test", "jonh@example.test"),
        ("a@example.test", "aa@example.test"),
        ("invalid-email", "invalid-email"),
    ],
)
def test_near_duplicate_email_uses_typographical_variation(
    original: str,
    expected: str,
) -> None:
    assert _near_duplicate_email(original) == expected


def test_imperfections_preserve_schema_and_expected_row_counts(
    distributed_and_imperfect_tables: tuple[dict, dict],
) -> None:
    distributed, imperfect = distributed_and_imperfect_tables
    duplicate_count = count_from_pct(len(distributed["contacts"]), 1.0)

    for table_name, expected_columns in CRM_COLUMN_CONTRACTS.items():
        assert imperfect[table_name].columns.tolist() == expected_columns
        expected_rows = len(distributed[table_name])
        if table_name == "contacts":
            expected_rows += duplicate_count
        assert len(imperfect[table_name]) == expected_rows

    assert imperfect["contacts"]["contact_id"].is_unique
    assert (
        imperfect["contact_campaigns"].duplicated(["contact_id", "campaign_id"]).sum()
        == 0
    )
    assert imperfect["support_cases"]["case_number"].is_unique


def test_contact_duplicates_have_new_ids_and_typographical_variations(
    distributed_and_imperfect_tables: tuple[dict, dict],
) -> None:
    distributed, imperfect = distributed_and_imperfect_tables
    duplicate_count = count_from_pct(len(distributed["contacts"]), 1.0)
    duplicates = imperfect["contacts"].tail(duplicate_count)
    original_variations = {
        (
            _near_duplicate_first_name(row.first_name),
            _near_duplicate_email(row.email),
        )
        for row in distributed["contacts"].itertuples(index=False)
    }

    assert duplicates["contact_id"].min() > distributed["contacts"]["contact_id"].max()
    assert all(
        (row.first_name, row.email) in original_variations
        for row in duplicates.itertuples(index=False)
    )
    assert set(duplicates["account_id"]) - {""} <= set(
        distributed["accounts"]["account_id"]
    )


def test_attribution_nulls_and_engagement_outliers_match_rates(
    distributed_and_imperfect_tables: tuple[dict, dict],
) -> None:
    distributed, imperfect = distributed_and_imperfect_tables
    expected_nulls = count_from_pct(len(distributed["contact_campaigns"]), 2.5)
    expected_outliers = count_from_pct(len(distributed["interactions"]), 0.5)

    weights = imperfect["contact_campaigns"]["attribution_weight"].astype(str)
    points = imperfect["interactions"]["engagement_points"].astype(int)
    assert int(weights.eq("").sum()) == expected_nulls
    assert int(points.between(50, 100).sum()) == expected_outliers
    assert points.ge(0).all()

    for _, memberships in imperfect["contact_campaigns"].groupby("contact_id"):
        weights_for_contact = memberships["attribution_weight"].astype(str)
        known_sum = sum(float(value) for value in weights_for_contact if value != "")
        if weights_for_contact.eq("").any():
            assert 0 <= known_sum < 1
        else:
            assert known_sum == pytest.approx(1.0)


def test_support_case_boundaries_rederive_sla_and_preserve_status_rules(
    distributed_and_imperfect_tables: tuple[dict, dict],
) -> None:
    _, imperfect = distributed_and_imperfect_tables
    support_cases = imperfect["support_cases"]
    boundary_timestamps = {
        "1900-01-01T00:00:00",
        "2038-01-19T00:00:00",
        "2099-12-31T00:00:00",
        "2026-08-01T00:00:00",
    }
    sla_hours = {"Low": 72, "Medium": 48, "High": 24, "Critical": 4}
    resolved_statuses = {"Resolved", "Closed"}

    assert boundary_timestamps <= set(support_cases["opened_at"])
    for row in support_cases.itertuples(index=False):
        opened = datetime.fromisoformat(row.opened_at)
        assert datetime.fromisoformat(row.sla_due_at) == opened + timedelta(
            hours=sla_hours[row.priority]
        )
        assert (row.resolved_at != "") == (row.status in resolved_statuses)
        assert datetime.fromisoformat(row.created_at) >= opened
        if row.resolved_at:
            assert datetime.fromisoformat(row.resolved_at) >= opened


def test_normal_business_state_nulls_are_not_additional_targets(
    distributed_and_imperfect_tables: tuple[dict, dict],
) -> None:
    distributed, imperfect = distributed_and_imperfect_tables
    for table_name, column_name in (
        ("campaigns", "end_date"),
        ("interactions", "campaign_id"),
        ("interactions", "account_id"),
        ("support_cases", "contact_id"),
        ("support_cases", "resolved_at"),
    ):
        assert imperfect[table_name][column_name].astype(str).eq("").tolist() == (
            distributed[table_name][column_name].astype(str).eq("").tolist()
        )


def test_imperfections_preserve_distributed_campaign_budgets(
    distributed_and_imperfect_tables: tuple[dict, dict],
) -> None:
    distributed, imperfect = distributed_and_imperfect_tables

    assert imperfect["campaigns"]["budget_amount"].equals(
        distributed["campaigns"]["budget_amount"]
    )


def test_imperfection_preflight_rejects_duplicate_membership_pairs(
    distributed_and_imperfect_tables: tuple[dict, dict],
) -> None:
    distributed, _ = distributed_and_imperfect_tables
    malformed = {
        table_name: table.copy(deep=True) for table_name, table in distributed.items()
    }
    malformed["contact_campaigns"].loc[1, ["contact_id", "campaign_id"]] = malformed[
        "contact_campaigns"
    ].loc[0, ["contact_id", "campaign_id"]]

    with pytest.raises(ValueError, match="duplicate primary keys"):
        CRMImperfectionInjector.for_profile("dev").apply_to_tables(malformed)


def test_imperfect_generation_is_reproducible() -> None:
    if not _dependencies_available():
        pytest.skip("numpy, pandas, and Faker are not installed")

    first = CRMImperfectionInjector.for_profile("dev").generate_imperfect_tables()
    second = CRMImperfectionInjector.for_profile("dev").generate_imperfect_tables()
    for table_name in CRM_COLUMN_CONTRACTS:
        assert first[table_name].equals(second[table_name])
