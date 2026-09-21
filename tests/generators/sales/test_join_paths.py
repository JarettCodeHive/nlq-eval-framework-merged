from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.core.csv_export import CSVExporter
from generators.sales.config import load_sales_config
from generators.sales.imperfections import SalesImperfectionInjector
from generators.sales.validators.join_paths import MANY_TO_MANY_CARDINALITY_SQL
from generators.sales.validators.join_paths import REGISTERED_JOIN_SQL
from generators.sales.validators.join_paths import SalesJoinPathValidator
from generators.sales.validators.join_paths import _join_count_sql


EXPECTED_JOIN_PATH_IDS = [f"sales_jp_{number:03d}" for number in range(1, 9)]


def _dependencies_available() -> bool:
    return all(
        importlib.util.find_spec(package) is not None
        for package in ("duckdb", "numpy", "pandas", "faker")
    )


pytestmark = pytest.mark.skipif(
    not _dependencies_available(),
    reason="duckdb, numpy, pandas, and Faker are not installed",
)


@pytest.fixture(scope="module")
def imperfect_tables() -> dict:
    return SalesImperfectionInjector.for_profile("dev").generate_imperfect_tables()


def test_config_contains_all_required_sales_join_paths() -> None:
    paths = load_sales_config()["join_path_requirements"]

    assert [path["id"] for path in paths] == EXPECTED_JOIN_PATH_IDS
    assert sum(path["required_result"] == "non_empty" for path in paths) == 6
    assert (
        sum(
            path["required_result"] == "non_empty_with_unmatched_parent_rows"
            for path in paths
        )
        == 2
    )


def test_registered_sql_covers_complex_and_analytical_paths() -> None:
    paths = {path["id"]: path for path in load_sales_config()["join_path_requirements"]}

    assert set(REGISTERED_JOIN_SQL) == {
        "sales_jp_005",
        "sales_jp_006",
        "sales_jp_007",
        "sales_jp_008",
    }
    assert "INNER JOIN quotations" in _join_count_sql(paths["sales_jp_005"])
    assert "INNER JOIN products" in _join_count_sql(paths["sales_jp_006"])
    assert "deals.stage = 'Won'" in _join_count_sql(paths["sales_jp_007"])
    assert "deals.close_date >= targets.period_start" in _join_count_sql(
        paths["sales_jp_008"]
    )


def test_simple_paths_use_configured_join_behavior() -> None:
    paths = {path["id"]: path for path in load_sales_config()["join_path_requirements"]}

    assert "LEFT JOIN deals" in _join_count_sql(paths["sales_jp_001"])
    assert "INNER JOIN leads" in _join_count_sql(paths["sales_jp_002"])
    assert "INNER JOIN quotations" in _join_count_sql(paths["sales_jp_003"])
    assert "INNER JOIN quotations" in _join_count_sql(paths["sales_jp_004"])


def test_cardinality_sql_covers_both_bridge_directions() -> None:
    assert set(MANY_TO_MANY_CARDINALITY_SQL) == {
        "quotations.deal_many_products",
        "quotations.product_many_deals",
    }
    assert (
        "COUNT(DISTINCT product_id) > 1"
        in MANY_TO_MANY_CARDINALITY_SQL["quotations.deal_many_products"]
    )
    assert (
        "COUNT(DISTINCT deal_id) > 1"
        in MANY_TO_MANY_CARDINALITY_SQL["quotations.product_many_deals"]
    )


def test_generated_join_path_validation_passes_all_checks() -> None:
    results = SalesJoinPathValidator.for_profile("dev").generate_and_validate()
    checks = _results_by_name(results)

    assert len(results) == 18
    assert all(result.passed for result in results)
    for join_id in EXPECTED_JOIN_PATH_IDS:
        assert checks[f"{join_id}.joined_rows"].passed
    for join_id in ("sales_jp_001", "sales_jp_007"):
        assert checks[f"{join_id}.unmatched_parent_rows"].passed
    assert checks["quotations.deal_many_products"].passed
    assert checks["quotations.product_many_deals"].passed


def test_join_validation_detects_absent_unmatched_leads(
    imperfect_tables: dict,
    tmp_path: Path,
) -> None:
    validator = SalesJoinPathValidator.for_profile("dev")
    modified = _copy_tables(imperfect_tables)
    deal_leads = set(modified["deals"]["lead_id"])
    modified["leads"] = modified["leads"][
        modified["leads"]["lead_id"].isin(deal_leads)
    ].reset_index(drop=True)

    checks = _results_by_name(
        validator._validate_csv_paths(_export_tables(validator, modified, tmp_path))
    )

    assert checks["sales_jp_001.joined_rows"].passed
    assert not checks["sales_jp_001.unmatched_parent_rows"].passed


def test_join_validation_detects_missing_quota_attainment_path(
    imperfect_tables: dict,
    tmp_path: Path,
) -> None:
    validator = SalesJoinPathValidator.for_profile("dev")
    modified = _copy_tables(imperfect_tables)
    modified["deals"].loc[modified["deals"]["stage"] == "Won", "stage"] = "Lost"

    checks = _results_by_name(
        validator._validate_csv_paths(_export_tables(validator, modified, tmp_path))
    )

    assert checks["sales_jp_007.unmatched_parent_rows"].passed
    assert not checks["sales_jp_008.joined_rows"].passed


def test_many_to_many_check_detects_one_to_one_bridge() -> None:
    import duckdb

    with duckdb.connect(database=":memory:") as connection:
        connection.execute(
            "CREATE TABLE quotations (deal_id INTEGER, product_id INTEGER)"
        )
        connection.execute("INSERT INTO quotations VALUES (1, 1), (2, 2)")
        results = SalesJoinPathValidator.for_profile(
            "dev"
        )._validate_many_to_many_cardinality(connection)

    assert all(not result.passed for result in results)


def test_exported_validation_reports_missing_csvs(tmp_path: Path) -> None:
    validator = SalesJoinPathValidator.for_profile("full")

    with pytest.raises(FileNotFoundError, match="leads.csv"):
        validator._csv_paths(tmp_path)


def test_validator_rejects_non_sales_settings() -> None:
    settings = GenerationSettings.from_config_files("crm", "dev")
    with pytest.raises(ValueError, match="only supports sales"):
        SalesJoinPathValidator(DeterministicGenerator(settings))


def _copy_tables(tables: dict) -> dict:
    return {name: table.copy(deep=True) for name, table in tables.items()}


def _export_tables(
    validator: SalesJoinPathValidator,
    tables: dict,
    output_dir: Path,
) -> dict[str, Path]:
    CSVExporter(validator.settings.csv_format).export_tables(
        tables=tables,
        table_order=validator.settings.table_order,
        output_dir=output_dir,
    )
    return validator._csv_paths(output_dir)


def _results_by_name(results: list) -> dict:
    return {result.check_name: result for result in results}
