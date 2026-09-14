from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from generators.crm.config import load_crm_config
from generators.crm.validators.config import validate_crm_config
from generators.crm.validators.join_paths import MANY_TO_MANY_CARDINALITY_SQL
from generators.crm.validators.join_paths import THREE_TABLE_JOIN_SQL
from generators.crm.validators.join_paths import CRMJoinPathValidator
from generators.crm.validators.join_paths import _join_count_sql


EXPECTED_JOIN_PATH_IDS = [f"crm_jp_{number:03d}" for number in range(1, 10)]


def _dependencies_available() -> bool:
    return all(
        importlib.util.find_spec(package) is not None
        for package in ("duckdb", "numpy", "pandas", "faker")
    )


def _results_by_name(results: list) -> dict:
    return {result.check_name: result for result in results}


def test_crm_config_contains_nine_engagement_join_paths() -> None:
    validate_crm_config()
    paths = load_crm_config()["join_path_requirements"]

    assert [path["id"] for path in paths] == EXPECTED_JOIN_PATH_IDS
    assert all(
        "opportunities" not in path["tables"] and "activities" not in path["tables"]
        for path in paths
    )
    assert sum(path["join_type"] == "left" for path in paths) == 5
    assert sum(path["join_type"] == "inner" for path in paths) == 4


def test_three_table_sql_matches_configured_paths() -> None:
    paths = {path["id"]: path for path in load_crm_config()["join_path_requirements"]}

    assert set(THREE_TABLE_JOIN_SQL) == {"crm_jp_003", "crm_jp_004", "crm_jp_009"}
    assert "INNER JOIN contacts" in _join_count_sql(paths["crm_jp_003"])
    assert "INNER JOIN interactions" in _join_count_sql(paths["crm_jp_003"])
    assert "INNER JOIN contact_campaigns" in _join_count_sql(paths["crm_jp_004"])
    assert "INNER JOIN campaigns" in _join_count_sql(paths["crm_jp_004"])
    assert "INNER JOIN support_cases" in _join_count_sql(paths["crm_jp_009"])
    assert "INNER JOIN contacts" in _join_count_sql(paths["crm_jp_009"])


def test_two_table_sql_uses_configured_join_behavior() -> None:
    paths = {path["id"]: path for path in load_crm_config()["join_path_requirements"]}

    assert "LEFT JOIN contacts" in _join_count_sql(paths["crm_jp_001"])
    assert "LEFT JOIN interactions" in _join_count_sql(paths["crm_jp_002"])
    assert "LEFT JOIN interactions" in _join_count_sql(paths["crm_jp_005"])
    assert "LEFT JOIN campaigns" in _join_count_sql(paths["crm_jp_006"])
    assert "INNER JOIN support_cases" in _join_count_sql(paths["crm_jp_007"])
    assert "LEFT JOIN support_cases" in _join_count_sql(paths["crm_jp_008"])


def test_cardinality_sql_covers_both_bridge_directions() -> None:
    assert set(MANY_TO_MANY_CARDINALITY_SQL) == {
        "contact_campaigns.contact_many_campaigns",
        "contact_campaigns.campaign_many_contacts",
    }
    assert (
        "COUNT(DISTINCT campaign_id) > 1"
        in MANY_TO_MANY_CARDINALITY_SQL["contact_campaigns.contact_many_campaigns"]
    )
    assert (
        "COUNT(DISTINCT contact_id) > 1"
        in MANY_TO_MANY_CARDINALITY_SQL["contact_campaigns.campaign_many_contacts"]
    )


def test_join_path_document_matches_engagement_contract() -> None:
    document = (
        Path("schemas/crm/docs/01_join_path_requirements.md")
        .read_text(encoding="utf-8")
        .lower()
    )

    assert all(join_id in document for join_id in EXPECTED_JOIN_PATH_IDS)
    assert "accounts -> support_cases -> contacts" in document
    assert "contacts -> contact_campaigns -> campaigns" in document
    assert "opportunities" not in document
    assert "activities" not in document


def test_join_path_validation_reports_missing_csvs(tmp_path: Path) -> None:
    validator = CRMJoinPathValidator.for_profile("full")

    with pytest.raises(FileNotFoundError, match="export-csvs"):
        validator._csv_paths(tmp_path)


def test_left_join_check_detects_missing_unmatched_rows() -> None:
    if importlib.util.find_spec("duckdb") is None:
        pytest.skip("duckdb is not installed")
    import duckdb

    join_path = load_crm_config()["join_path_requirements"][0]
    with duckdb.connect(database=":memory:") as connection:
        connection.execute("CREATE TABLE accounts (account_id INTEGER)")
        connection.execute(
            "CREATE TABLE contacts (contact_id INTEGER, account_id INTEGER)"
        )
        connection.execute("INSERT INTO accounts VALUES (1)")
        connection.execute("INSERT INTO contacts VALUES (1, 1)")
        results = CRMJoinPathValidator.for_profile("dev")._validate_join_path(
            connection, join_path
        )

    checks = _results_by_name(results)
    assert checks["crm_jp_001.joined_rows"].passed
    assert not checks["crm_jp_001.unmatched_parent_rows"].passed


def test_many_to_many_check_detects_one_to_one_bridge() -> None:
    if importlib.util.find_spec("duckdb") is None:
        pytest.skip("duckdb is not installed")
    import duckdb

    with duckdb.connect(database=":memory:") as connection:
        connection.execute(
            "CREATE TABLE contact_campaigns "
            "(contact_id INTEGER, campaign_id INTEGER)"
        )
        connection.execute("INSERT INTO contact_campaigns VALUES (1, 1), (2, 2)")
        results = CRMJoinPathValidator.for_profile(
            "dev"
        )._validate_many_to_many_cardinality(connection)

    assert all(not result.passed for result in results)


def test_generated_join_path_validation_passes_all_paths() -> None:
    if not _dependencies_available():
        pytest.skip("duckdb, numpy, pandas, and Faker are not installed")

    results = CRMJoinPathValidator.for_profile("dev").generate_and_validate()
    checks = _results_by_name(results)

    assert len(results) == 23
    assert all(result.passed for result in results)
    for join_id in EXPECTED_JOIN_PATH_IDS:
        assert checks[f"{join_id}.joined_rows"].passed
    for join_id in (
        "crm_jp_001",
        "crm_jp_002",
        "crm_jp_005",
        "crm_jp_006",
        "crm_jp_008",
    ):
        assert checks[f"{join_id}.unmatched_parent_rows"].passed
    assert checks["contact_campaigns.contact_many_campaigns"].passed
    assert checks["contact_campaigns.campaign_many_contacts"].passed
