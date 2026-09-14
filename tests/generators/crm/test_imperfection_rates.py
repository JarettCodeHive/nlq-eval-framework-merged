from __future__ import annotations

from datetime import datetime
import importlib.util
from pathlib import Path

import pytest

from generators.crm.validators.imperfection_rates import CRMImperfectionRateValidator
from generators.crm.validators.imperfection_rates import NULL_RATE_TARGETS
from generators.crm.validators.imperfection_rates import _is_valid_near_duplicate


def _dependencies_available() -> bool:
    return all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    )


def test_expected_dev_imperfection_counts_are_deterministic() -> None:
    validator = CRMImperfectionRateValidator.for_profile("dev")

    assert validator.expected_duplicate_count() == 3
    assert validator.expected_null_counts() == {
        "contact_campaigns.attribution_weight": 19,
    }
    assert validator.expected_outlier_count() == 5


def test_only_controlled_null_target_is_rate_validated() -> None:
    validator = CRMImperfectionRateValidator.for_profile("dev")

    assert NULL_RATE_TARGETS == {
        "contact_campaigns.attribution_weight": (
            "contact_campaigns",
            "attribution_weight",
        )
    }
    business_state_nulls = {
        "campaigns.end_date",
        "interactions.account_id",
        "interactions.campaign_id",
        "support_cases.contact_id",
        "support_cases.resolved_at",
    }
    assert business_state_nulls.isdisjoint(validator.expected_null_counts())


@pytest.mark.parametrize(
    ("first_name", "email", "expected"),
    [
        ("Jonh", "john.smiht@example.test", True),
        ("John Jr", "john.smiht@example.test", False),
        ("Jonh", "john.smith@example.test", False),
        ("Jonh", "missing@example.test", False),
    ],
)
def test_near_duplicate_validation_uses_typographical_variation(
    first_name: str,
    email: str,
    expected: bool,
) -> None:
    class DuplicateRow:
        pass

    row = DuplicateRow()
    row.first_name = first_name
    row.email = email

    assert (
        _is_valid_near_duplicate(row, {("Jonh", "john.smiht@example.test")}) is expected
    )


def test_expected_full_imperfection_counts_are_deterministic() -> None:
    validator = CRMImperfectionRateValidator.for_profile("full")

    assert validator.expected_duplicate_count() == 480
    assert validator.expected_null_counts() == {
        "contact_campaigns.attribution_weight": 4500,
    }
    assert validator.expected_outlier_count() == 1000


def test_exported_imperfection_rate_validation_reports_missing_csvs(
    tmp_path: Path,
) -> None:
    validator = CRMImperfectionRateValidator.for_profile("full")

    with pytest.raises(FileNotFoundError, match="accounts.csv"):
        validator._csv_paths(tmp_path)


def test_boundary_validation_detects_invalid_derived_timestamp() -> None:
    if importlib.util.find_spec("pandas") is None:
        pytest.skip("pandas is not installed")
    import pandas as pd

    validator = CRMImperfectionRateValidator.for_profile("dev")
    rows = []
    for value in validator.config["boundary_dates"]:
        opened = datetime.fromisoformat(f"{value}T00:00:00")
        rows.append(
            {
                "opened_at": opened.isoformat(),
                "sla_due_at": opened.isoformat(),
                "resolved_at": "",
                "priority": "High",
                "status": "New",
            }
        )
    tables = {"support_cases": pd.DataFrame(rows)}

    results = validator._validate_boundary_values(tables)
    checks = {result.check_name: result for result in results}

    assert checks["support_cases.opened_at.boundary_values"].passed
    assert not checks["support_cases.boundary_sla_derivation"].passed
    assert checks["support_cases.boundary_resolution_derivation"].passed


def test_generated_imperfection_rate_validation_when_dependencies_are_installed() -> (
    None
):
    if not _dependencies_available():
        pytest.skip("numpy, pandas, and Faker are not installed")

    results = CRMImperfectionRateValidator.for_profile("dev").generate_and_validate()
    checks = {result.check_name: result for result in results}

    assert len(results) == 8
    assert all(result.passed for result in results)
    assert checks["contacts.near_duplicate_rate"].passed
    assert checks["contact_campaigns.attribution_weight.null_rate"].passed
    assert checks["interactions.engagement_points.outlier_rate"].passed
    assert checks["interactions.engagement_points.outlier_range"].passed
    assert checks["support_cases.opened_at.boundary_values"].passed
