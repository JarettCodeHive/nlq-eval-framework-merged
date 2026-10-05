from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from generators.core.csv_export import CSVExporter
from generators.logistics.imperfections import LogisticsImperfectionInjector
from generators.logistics.validators.fk_integrity import LogisticsDuckDBFKValidator
from generators.logistics.validators.fk_integrity import MANUAL_FK_CHECKS


@pytest.fixture(scope="module")
def imperfect_tables() -> dict:
    return LogisticsImperfectionInjector.for_profile("dev").generate_imperfect_tables()


def test_validator_uses_canonical_schema_and_explicit_checks() -> None:
    validator = LogisticsDuckDBFKValidator.for_profile("full")

    assert validator.settings.schema_source.name == "logistics_ddl.sql"
    assert set(MANUAL_FK_CHECKS) == {
        "shipments.order_id.fk",
        "shipments.carrier_id.fk",
        "inventory.warehouse_id.fk",
    }
    assert "pragma foreign_key_check" not in " ".join(MANUAL_FK_CHECKS.values()).lower()


def test_generated_fk_and_declared_orphan_validation_passes() -> None:
    results = LogisticsDuckDBFKValidator.for_profile("dev").generate_and_validate()
    checks = _results_by_name(results)

    assert len(results) == 12
    assert all(result.passed for result in results)
    assert checks["schema.duckdb_ddl"].passed
    assert checks["orders.warehouse_id.declared_orphan_count"].passed
    assert checks["orders.warehouse_id.orphan_namespace"].passed


def test_constrained_load_rejects_physical_shipment_orphan(
    imperfect_tables: dict,
    tmp_path: Path,
) -> None:
    validator = LogisticsDuckDBFKValidator.for_profile("dev")
    malformed = _copy_tables(imperfect_tables)
    malformed["shipments"].at[0, "order_id"] = 999999

    checks = _results_by_name(
        validator._validate_csv_paths(_export_tables(validator, malformed, tmp_path))
    )

    assert checks["schema.duckdb_ddl"].passed
    assert not checks["shipments.duckdb_load"].passed


def test_analytical_check_rejects_wrong_orphan_namespace(
    imperfect_tables: dict,
    tmp_path: Path,
) -> None:
    validator = LogisticsDuckDBFKValidator.for_profile("dev")
    malformed = _copy_tables(imperfect_tables)
    warehouses = set(malformed["warehouses"]["warehouse_id"])
    position = next(
        index
        for index, value in enumerate(malformed["orders"]["warehouse_id"])
        if value != "" and value not in warehouses
    )
    malformed["orders"].at[position, "warehouse_id"] = 800000000

    checks = _results_by_name(
        validator._validate_csv_paths(_export_tables(validator, malformed, tmp_path))
    )

    assert checks["orders.duckdb_load"].passed
    assert checks["orders.warehouse_id.declared_orphan_count"].passed
    assert not checks["orders.warehouse_id.orphan_namespace"].passed


def test_analytical_check_rejects_excess_warehouse_nulls(
    imperfect_tables: dict,
    tmp_path: Path,
) -> None:
    validator = LogisticsDuckDBFKValidator.for_profile("dev")
    malformed = _copy_tables(imperfect_tables)
    warehouses = set(malformed["warehouses"]["warehouse_id"])
    position = next(
        index
        for index, value in enumerate(malformed["orders"]["warehouse_id"])
        if value in warehouses
    )
    malformed["orders"].at[position, "warehouse_id"] = ""

    checks = _results_by_name(
        validator._validate_csv_paths(_export_tables(validator, malformed, tmp_path))
    )

    assert checks["orders.duckdb_load"].passed
    assert not checks["orders.warehouse_id.null_count"].passed


def test_invalid_ddl_is_reported(
    imperfect_tables: dict,
    tmp_path: Path,
) -> None:
    validator = LogisticsDuckDBFKValidator.for_profile("dev")
    ddl_path = tmp_path / "invalid.sql"
    ddl_path.write_text("CREATE TABLE broken (", encoding="utf-8")
    validator.settings = replace(validator.settings, schema_source=ddl_path)

    results = validator._validate_csv_paths(
        _export_tables(validator, imperfect_tables, tmp_path / "csv")
    )

    assert len(results) == 1
    assert results[0].check_name == "schema.duckdb_ddl"
    assert not results[0].passed


def test_missing_csvs_are_reported(tmp_path: Path) -> None:
    validator = LogisticsDuckDBFKValidator.for_profile("full")

    with pytest.raises(FileNotFoundError, match="carriers.csv"):
        validator.validate_csv_directory(tmp_path)


def _copy_tables(tables: dict) -> dict:
    return {name: table.copy(deep=True) for name, table in tables.items()}


def _export_tables(
    validator: LogisticsDuckDBFKValidator,
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
