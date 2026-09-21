from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.core.csv_export import CSVExporter
from generators.finance.config import load_finance_config
from generators.finance.imperfections import FinanceImperfectionInjector
from generators.finance.validators.join_paths import FinanceJoinPathValidator
from generators.finance.validators.join_paths import MANY_TO_MANY_CARDINALITY_SQL
from generators.finance.validators.join_paths import REGISTERED_JOIN_SQL
from generators.finance.validators.join_paths import _join_count_sql


EXPECTED_JOIN_PATH_IDS = [f"finance_jp_{number:03d}" for number in range(1, 11)]


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
    return FinanceImperfectionInjector.for_profile(
        "dev"
    ).generate_imperfect_tables()


def test_config_contains_all_required_finance_join_paths() -> None:
    paths = load_finance_config()["join_path_requirements"]

    assert [path["id"] for path in paths] == EXPECTED_JOIN_PATH_IDS
    assert paths[0]["required_result"] == "non_empty_with_unmatched_parent_rows"
    assert paths[4]["required_result"] == (
        "non_empty_with_matched_and_unmatched_rows"
    )
    assert paths[5]["required_result"] == "complete_unique_lookup_coverage"
    assert paths[9]["required_result"] == (
        "non_empty_with_matched_and_unmatched_parent_rows"
    )


def test_registered_sql_covers_complex_finance_paths() -> None:
    paths = {
        path["id"]: path
        for path in load_finance_config()["join_path_requirements"]
    }

    assert set(REGISTERED_JOIN_SQL) == {
        "finance_jp_005",
        "finance_jp_006",
        "finance_jp_009",
        "finance_jp_010",
    }
    assert "parent_account" in _join_count_sql(paths["finance_jp_005"])
    assert "INNER JOIN fx_rates" in _join_count_sql(paths["finance_jp_006"])
    assert "INNER JOIN accounts" in _join_count_sql(paths["finance_jp_009"])
    assert "period_actuals" in _join_count_sql(paths["finance_jp_010"])


def test_simple_paths_use_configured_join_behavior() -> None:
    paths = {
        path["id"]: path
        for path in load_finance_config()["join_path_requirements"]
    }

    assert "LEFT JOIN ledger_entries" in _join_count_sql(paths["finance_jp_001"])
    assert "INNER JOIN transactions" in _join_count_sql(paths["finance_jp_002"])
    assert "INNER JOIN accounts" in _join_count_sql(paths["finance_jp_003"])
    assert "INNER JOIN accounts" in _join_count_sql(paths["finance_jp_004"])
    assert "fx_rates.rate IS NOT NULL" in _join_count_sql(paths["finance_jp_007"])
    assert "fx_rates.rate IS NULL" in _join_count_sql(paths["finance_jp_008"])


def test_cardinality_sql_covers_both_ledger_directions() -> None:
    assert set(MANY_TO_MANY_CARDINALITY_SQL) == {
        "ledger_entries.transaction_many_accounts",
        "ledger_entries.account_many_transactions",
    }
    assert (
        "COUNT(DISTINCT account_id) > 1"
        in MANY_TO_MANY_CARDINALITY_SQL[
            "ledger_entries.transaction_many_accounts"
        ]
    )
    assert (
        "COUNT(DISTINCT transaction_id) > 1"
        in MANY_TO_MANY_CARDINALITY_SQL[
            "ledger_entries.account_many_transactions"
        ]
    )


def test_generated_join_path_validation_passes_all_checks() -> None:
    results = FinanceJoinPathValidator.for_profile("dev").generate_and_validate()
    checks = _results_by_name(results)

    assert len(results) == 25
    assert all(result.passed for result in results)
    for join_id in EXPECTED_JOIN_PATH_IDS:
        assert checks[f"{join_id}.joined_rows"].passed
    for join_id in ("finance_jp_001", "finance_jp_005", "finance_jp_010"):
        assert checks[f"{join_id}.unmatched_parent_rows"].passed
    for join_id in ("finance_jp_005", "finance_jp_010"):
        assert checks[f"{join_id}.matched_parent_rows"].passed
    assert checks["finance_jp_006.lookup_coverage"].passed
    assert checks["finance_jp_006.unique_lookup"].passed
    assert checks["ledger_entries.transaction_many_accounts"].passed
    assert checks["ledger_entries.account_many_transactions"].passed


def test_join_validation_detects_absent_unposted_transactions(
    imperfect_tables: dict,
    tmp_path: Path,
) -> None:
    validator = FinanceJoinPathValidator.for_profile("dev")
    modified = _copy_tables(imperfect_tables)
    posted_ids = set(modified["ledger_entries"]["transaction_id"])
    modified["transactions"] = modified["transactions"][
        modified["transactions"]["transaction_id"].isin(posted_ids)
    ].reset_index(drop=True)

    checks = _results_by_name(
        validator._validate_csv_paths(_export_tables(validator, modified, tmp_path))
    )

    assert checks["finance_jp_001.joined_rows"].passed
    assert not checks["finance_jp_001.unmatched_parent_rows"].passed


def test_join_validation_detects_missing_account_hierarchy_match(
    imperfect_tables: dict,
    tmp_path: Path,
) -> None:
    validator = FinanceJoinPathValidator.for_profile("dev")
    modified = _copy_tables(imperfect_tables)
    modified["accounts"]["parent_account_id"] = ""

    checks = _results_by_name(
        validator._validate_csv_paths(_export_tables(validator, modified, tmp_path))
    )

    assert checks["finance_jp_005.unmatched_parent_rows"].passed
    assert not checks["finance_jp_005.matched_parent_rows"].passed


def test_join_validation_detects_missing_fx_lookup(
    imperfect_tables: dict,
    tmp_path: Path,
) -> None:
    validator = FinanceJoinPathValidator.for_profile("dev")
    modified = _copy_tables(imperfect_tables)
    transaction = modified["transactions"].iloc[0]
    matches = (
        modified["fx_rates"]["from_currency"].eq(transaction["source_currency"])
        & modified["fx_rates"]["to_currency"].eq(transaction["target_currency"])
        & modified["fx_rates"]["rate_date"].eq(transaction["transaction_date"])
    )
    modified["fx_rates"] = modified["fx_rates"][~matches].reset_index(drop=True)

    checks = _results_by_name(
        validator._validate_csv_paths(_export_tables(validator, modified, tmp_path))
    )

    assert not checks["finance_jp_006.lookup_coverage"].passed


def test_join_validation_detects_missing_null_fx_path(
    imperfect_tables: dict,
    tmp_path: Path,
) -> None:
    validator = FinanceJoinPathValidator.for_profile("dev")
    modified = _copy_tables(imperfect_tables)
    modified["fx_rates"].loc[
        modified["fx_rates"]["rate"].astype(str).eq(""), "rate"
    ] = "1.000000"

    checks = _results_by_name(
        validator._validate_csv_paths(_export_tables(validator, modified, tmp_path))
    )

    assert not checks["finance_jp_008.joined_rows"].passed


def test_many_to_many_check_detects_one_to_one_bridge() -> None:
    import duckdb

    with duckdb.connect(database=":memory:") as connection:
        connection.execute(
            """
            CREATE TABLE ledger_entries (
                transaction_id INTEGER,
                account_id INTEGER
            )
            """
        )
        connection.execute("INSERT INTO ledger_entries VALUES (1, 1), (2, 2)")
        results = FinanceJoinPathValidator.for_profile(
            "dev"
        )._validate_many_to_many_cardinality(connection)

    assert all(not result.passed for result in results)


def test_exported_validation_reports_missing_csvs(tmp_path: Path) -> None:
    validator = FinanceJoinPathValidator.for_profile("full")

    with pytest.raises(FileNotFoundError, match="accounts.csv"):
        validator._csv_paths(tmp_path)


def test_validator_rejects_non_finance_settings() -> None:
    settings = GenerationSettings.from_config_files("sales", "dev")
    with pytest.raises(ValueError, match="only supports finance"):
        FinanceJoinPathValidator(DeterministicGenerator(settings))


def _copy_tables(tables: dict) -> dict:
    return {name: table.copy(deep=True) for name, table in tables.items()}


def _export_tables(
    validator: FinanceJoinPathValidator,
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
