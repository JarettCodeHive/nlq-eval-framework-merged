from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
import importlib.util

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.crm.config import load_base_config
from generators.crm.config import load_crm_config
from generators.crm.distributions import CRMDistributionApplier
from generators.crm.generator import CRMBaseEntityGenerator
from generators.crm.generator import CRM_COLUMN_CONTRACTS


def _dependencies_available() -> bool:
    return all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    )


@pytest.fixture(scope="module")
def base_and_distributed_tables() -> tuple[dict, dict]:
    if not _dependencies_available():
        pytest.skip("numpy, pandas, and Faker are not installed")
    base = CRMBaseEntityGenerator.for_profile("dev").generate_tables()
    source_snapshot = {
        table_name: table.copy(deep=True) for table_name, table in base.items()
    }
    distributed = CRMDistributionApplier.for_profile("dev").apply_to_tables(base)
    for table_name in base:
        assert base[table_name].equals(source_snapshot[table_name])
    return base, distributed


def test_revised_crm_distribution_settings_are_loaded() -> None:
    applier = CRMDistributionApplier.for_profile("dev")

    assert applier.settings.distributions["campaign_budget"]["alpha"] == 1.16
    assert applier.settings.distributions["interaction_frequency"]["lambda"] == 3.2
    assert applier.settings.distributions["date_clustering"]["components"] == 3
    assert "revenue_amount" not in applier.settings.distributions


def test_distributions_preserve_table_shapes_and_inputs(
    base_and_distributed_tables: tuple[dict, dict],
) -> None:
    base, distributed = base_and_distributed_tables

    assert tuple(distributed) == tuple(CRM_COLUMN_CONTRACTS)
    for table_name, expected_columns in CRM_COLUMN_CONTRACTS.items():
        assert distributed[table_name].columns.tolist() == expected_columns
        assert len(distributed[table_name]) == len(base[table_name])

    base_budgets = base["campaigns"]["budget_amount"].astype(str)
    budget_rules = load_crm_config()["generation_rules"]["campaigns"]["budget"]
    assert (
        base_budgets[base_budgets.ne("")]
        .astype(float)
        .between(budget_rules["minimum_amount"], budget_rules["maximum_amount"])
        .all()
    )
    assert not base["campaigns"]["budget_amount"].equals(
        distributed["campaigns"]["budget_amount"]
    )
    assert not base["accounts"]["created_at"].equals(
        distributed["accounts"]["created_at"]
    )
    assert not base["support_cases"]["opened_at"].equals(
        distributed["support_cases"]["opened_at"]
    )


def test_distribution_preflight_rejects_missing_tables(
    base_and_distributed_tables: tuple[dict, dict],
) -> None:
    base, _ = base_and_distributed_tables
    malformed = dict(base)
    malformed.pop("support_cases")

    with pytest.raises(ValueError, match="table order differs"):
        CRMDistributionApplier.for_profile("dev").apply_to_tables(malformed)


def test_campaign_budget_distribution_preserves_nulls_and_bounds(
    base_and_distributed_tables: tuple[dict, dict],
) -> None:
    base, distributed = base_and_distributed_tables
    base_budgets = base["campaigns"]["budget_amount"].astype(str)
    budgets = distributed["campaigns"]["budget_amount"].astype(str)

    assert budgets.eq("").tolist() == base_budgets.eq("").tolist()
    populated = budgets[budgets.ne("")]
    assert populated.map(lambda value: Decimal(value).as_tuple().exponent == -2).all()
    assert populated.astype(float).between(5000, 2_000_000).all()
    assert populated.astype(float).median() < populated.astype(float).max()


def test_campaign_budget_override_reaches_distribution_applier() -> None:
    base_config = deepcopy(load_base_config())
    crm_config = deepcopy(load_crm_config())
    crm_config["distributions"]["campaign_budget"].update(
        {
            "min_amount": 12_345,
            "max_amount": 12_345,
            "scale": 3,
        }
    )
    settings = GenerationSettings.from_configs(base_config, crm_config, "dev")
    generator = DeterministicGenerator(settings)
    base = CRMBaseEntityGenerator(generator).generate_tables()

    distributed = CRMDistributionApplier(generator).apply_to_tables(base)

    populated = distributed["campaigns"]["budget_amount"].astype(str)
    populated = populated[populated.ne("")]
    assert set(populated) == {"12345.000"}


def test_poisson_interactions_preserve_relationships(
    base_and_distributed_tables: tuple[dict, dict],
) -> None:
    base, distributed = base_and_distributed_tables
    contacts = distributed["contacts"]
    memberships = distributed["contact_campaigns"]
    interactions = distributed["interactions"]

    assert (
        interactions["campaign_id"].astype(str).eq("").tolist()
        == base["interactions"]["campaign_id"].astype(str).eq("").tolist()
    )
    assert not interactions[["contact_id", "campaign_id"]].equals(
        base["interactions"][["contact_id", "campaign_id"]]
    )
    valid_pairs = set(
        zip(memberships["contact_id"], memberships["campaign_id"], strict=True)
    )
    attributed = interactions[interactions["campaign_id"].astype(str).ne("")]
    assert all(
        (int(row.contact_id), int(row.campaign_id)) in valid_pairs
        for row in attributed.itertuples(index=False)
    )

    account_by_contact = dict(
        zip(contacts["contact_id"], contacts["account_id"], strict=True)
    )
    assert all(
        row.account_id == "" or row.account_id == account_by_contact[row.contact_id]
        for row in interactions.itertuples(index=False)
    )
    counts = interactions.groupby("contact_id").size()
    assert counts.nunique() > 1


def test_clustered_dates_preserve_campaign_and_case_chronology(
    base_and_distributed_tables: tuple[dict, dict],
) -> None:
    _, tables = base_and_distributed_tables
    campaigns = tables["campaigns"]
    memberships = tables["contact_campaigns"]
    interactions = tables["interactions"]
    support_cases = tables["support_cases"]
    reference = datetime.fromisoformat("2026-08-01T23:59:59")

    campaign_windows = {}
    for row in campaigns.itertuples(index=False):
        start = datetime.fromisoformat(f"{row.start_date}T00:00:00")
        if row.end_date:
            end = datetime.fromisoformat(f"{row.end_date}T23:59:59")
        elif start > reference:
            end = start + timedelta(days=90, hours=23, minutes=59, seconds=59)
        else:
            end = reference
        campaign_windows[row.campaign_id] = (start, end)
        assert datetime.fromisoformat(row.created_at) <= start
        if row.end_date:
            assert row.end_date >= row.start_date

    assert all(
        campaign_windows[row.campaign_id][0]
        <= datetime.fromisoformat(row.first_touch_at)
        <= datetime.fromisoformat(row.last_touch_at)
        <= campaign_windows[row.campaign_id][1]
        for row in memberships.itertuples(index=False)
    )
    assert all(
        row.campaign_id == ""
        or campaign_windows[row.campaign_id][0]
        <= datetime.fromisoformat(row.interaction_at)
        <= campaign_windows[row.campaign_id][1]
        for row in interactions.itertuples(index=False)
    )

    sla_hours = {"Low": 72, "Medium": 48, "High": 24, "Critical": 4}
    resolved_statuses = {"Resolved", "Closed"}
    for row in support_cases.itertuples(index=False):
        opened = datetime.fromisoformat(row.opened_at)
        assert datetime.fromisoformat(row.sla_due_at) == opened + timedelta(
            hours=sla_hours[row.priority]
        )
        assert (row.resolved_at != "") == (row.status in resolved_statuses)
        if row.resolved_at:
            assert datetime.fromisoformat(row.resolved_at) >= opened


def test_distributed_generation_is_reproducible() -> None:
    if not _dependencies_available():
        pytest.skip("numpy, pandas, and Faker are not installed")

    first = CRMDistributionApplier.for_profile("dev").generate_distributed_tables()
    second = CRMDistributionApplier.for_profile("dev").generate_distributed_tables()

    for table_name in CRM_COLUMN_CONTRACTS:
        assert first[table_name].equals(second[table_name])
