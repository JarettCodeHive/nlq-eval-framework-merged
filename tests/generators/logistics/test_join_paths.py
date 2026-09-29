from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from generators.core.csv_export import CSVExporter
from generators.logistics.config import load_logistics_config
from generators.logistics.imperfections import LogisticsImperfectionInjector
from generators.logistics.validators.join_paths import LogisticsJoinPathValidator
from generators.logistics.validators.join_paths import MANY_TO_MANY_CARDINALITY_SQL
from generators.logistics.validators.join_paths import REGISTERED_JOIN_SQL
from generators.logistics.validators.join_paths import WAREHOUSE_UNMATCHED_SQL


EXPECTED_JOIN_PATH_IDS = [f"logistics_jp_{number:03d}" for number in range(1, 7)]


@pytest.fixture(scope="module")
def imperfect_tables() -> dict:
    return LogisticsImperfectionInjector.for_profile(
        "dev"
    ).generate_imperfect_tables()


def test_config_and_registered_sql_cover_required_paths() -> None:
    paths = load_logistics_config()["join_path_requirements"]

    assert [path["id"] for path in paths] == EXPECTED_JOIN_PATH_IDS
    assert set(REGISTERED_JOIN_SQL) == {"logistics_jp_006"}
    assert set(WAREHOUSE_UNMATCHED_SQL) == {
        "logistics_jp_004.null_unmatched_rows",
        "logistics_jp_004.orphan_unmatched_rows",
    }


def test_many_to_many_sql_covers_both_shipment_directions() -> None:
    assert set(MANY_TO_MANY_CARDINALITY_SQL) == {
        "shipments.order_many_carriers",
        "shipments.carrier_many_orders",
    }


def test_generated_join_path_validation_passes() -> None:
    results = LogisticsJoinPathValidator.for_profile(
        "dev"
    ).generate_and_validate()
    checks = _results_by_name(results)

    assert len(results) == 17
    assert all(result.passed for result in results)
    for join_id in EXPECTED_JOIN_PATH_IDS:
        assert checks[f"{join_id}.joined_rows"].passed
    assert checks["logistics_jp_004.unmatched_parent_rows"].passed
    assert checks["logistics_jp_004.null_unmatched_rows"].passed
    assert checks["logistics_jp_004.orphan_unmatched_rows"].passed
    assert checks["shipments.order_many_carriers"].passed
    assert checks["shipments.carrier_many_orders"].passed


def test_left_join_requires_null_and_orphan_unmatched_cases(
    imperfect_tables: dict,
    tmp_path: Path,
) -> None:
    validator = LogisticsJoinPathValidator.for_profile("dev")
    modified = _copy_tables(imperfect_tables)
    valid_warehouse = int(modified["warehouses"].iloc[0]["warehouse_id"])
    warehouse_ids = set(modified["warehouses"]["warehouse_id"])
    modified["orders"]["warehouse_id"] = modified["orders"]["warehouse_id"].map(
        lambda value: (
            valid_warehouse
            if value == "" or value not in warehouse_ids
            else value
        )
    )

    checks = _results_by_name(
        validator._validate_csv_paths(_export_tables(validator, modified, tmp_path))
    )

    assert not checks["logistics_jp_004.unmatched_parent_rows"].passed
    assert not checks["logistics_jp_004.null_unmatched_rows"].passed
    assert not checks["logistics_jp_004.orphan_unmatched_rows"].passed


def test_null_and_orphan_unmatched_checks_are_independent(
    imperfect_tables: dict,
    tmp_path: Path,
) -> None:
    validator = LogisticsJoinPathValidator.for_profile("dev")
    valid_warehouse = int(imperfect_tables["warehouses"].iloc[0]["warehouse_id"])

    no_nulls = _copy_tables(imperfect_tables)
    no_nulls["orders"]["warehouse_id"] = no_nulls["orders"]["warehouse_id"].map(
        lambda value: valid_warehouse if value == "" else value
    )
    null_checks = _results_by_name(
        validator._validate_csv_paths(
            _export_tables(validator, no_nulls, tmp_path / "no-nulls")
        )
    )
    assert not null_checks["logistics_jp_004.null_unmatched_rows"].passed
    assert null_checks["logistics_jp_004.orphan_unmatched_rows"].passed

    no_orphans = _copy_tables(imperfect_tables)
    warehouse_ids = set(no_orphans["warehouses"]["warehouse_id"])
    no_orphans["orders"]["warehouse_id"] = no_orphans["orders"][
        "warehouse_id"
    ].map(
        lambda value: (
            valid_warehouse
            if value != "" and value not in warehouse_ids
            else value
        )
    )
    orphan_checks = _results_by_name(
        validator._validate_csv_paths(
            _export_tables(validator, no_orphans, tmp_path / "no-orphans")
        )
    )
    assert orphan_checks["logistics_jp_004.null_unmatched_rows"].passed
    assert not orphan_checks["logistics_jp_004.orphan_unmatched_rows"].passed


def test_many_to_many_check_detects_one_to_one_shipments() -> None:
    with duckdb.connect(database=":memory:") as connection:
        connection.execute(
            "CREATE TABLE shipments (order_id INTEGER, carrier_id INTEGER)"
        )
        connection.execute("INSERT INTO shipments VALUES (1, 1), (2, 2)")
        results = LogisticsJoinPathValidator.for_profile(
            "dev"
        )._validate_many_to_many_cardinality(connection)

    assert all(not result.passed for result in results)


def test_missing_csvs_are_reported(tmp_path: Path) -> None:
    validator = LogisticsJoinPathValidator.for_profile("full")

    with pytest.raises(FileNotFoundError, match="carriers.csv"):
        validator.validate_csv_directory(tmp_path)


def _copy_tables(tables: dict) -> dict:
    return {name: table.copy(deep=True) for name, table in tables.items()}


def _export_tables(
    validator: LogisticsJoinPathValidator,
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
