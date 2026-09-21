from __future__ import annotations

from dataclasses import replace
import importlib.util
from pathlib import Path

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.core.csv_export import CSVExporter
from generators.sales.imperfections import SalesImperfectionInjector
from generators.sales.validators.fk_integrity import MANUAL_FK_CHECKS
from generators.sales.validators.fk_integrity import SalesDuckDBFKValidator


EXPECTED_TABLE_ORDER = ("leads", "deals", "products", "quotations", "targets")


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


def test_sales_duckdb_validator_uses_canonical_schema() -> None:
    validator = SalesDuckDBFKValidator.for_profile("full")

    assert validator.settings.table_order == EXPECTED_TABLE_ORDER
    assert validator.settings.output_path.name == "dataset-v1.0.0"
    assert validator.settings.schema_source.name == "sales_ddl.sql"


def test_manual_checks_cover_only_physical_sales_foreign_keys() -> None:
    assert set(MANUAL_FK_CHECKS) == {
        "deals.lead_id.fk",
        "quotations.deal_id.fk",
        "quotations.product_id.fk",
    }
    sql = " ".join(MANUAL_FK_CHECKS.values()).lower()
    assert "pragma foreign_key_check" not in sql
    assert "rep_name" not in sql


def test_generated_fk_validation_passes_all_checks() -> None:
    results = SalesDuckDBFKValidator.for_profile("dev").generate_and_validate()
    checks = _results_by_name(results)

    assert len(results) == 9
    assert all(result.passed for result in results)
    assert checks["schema.duckdb_ddl"].passed
    for table_name in EXPECTED_TABLE_ORDER:
        assert checks[f"{table_name}.duckdb_load"].passed


@pytest.mark.parametrize(
    ("table_name", "column_name", "invalid_value", "failed_load"),
    [
        ("deals", "lead_id", 999999, "deals.duckdb_load"),
        ("quotations", "deal_id", 999999, "quotations.duckdb_load"),
        ("quotations", "product_id", 999999, "quotations.duckdb_load"),
    ],
)
def test_constrained_load_rejects_orphan_foreign_keys(
    imperfect_tables: dict,
    tmp_path: Path,
    table_name: str,
    column_name: str,
    invalid_value: int,
    failed_load: str,
) -> None:
    validator = SalesDuckDBFKValidator.for_profile("dev")
    malformed = _copy_tables(imperfect_tables)
    malformed[table_name].loc[0, column_name] = invalid_value

    results = validator._validate_csv_paths(
        _export_tables(validator, malformed, tmp_path)
    )
    checks = _results_by_name(results)

    assert checks["schema.duckdb_ddl"].passed
    assert not checks[failed_load].passed


def test_rep_name_is_not_treated_as_a_foreign_key(
    imperfect_tables: dict,
    tmp_path: Path,
) -> None:
    validator = SalesDuckDBFKValidator.for_profile("dev")
    modified = _copy_tables(imperfect_tables)
    modified["deals"].loc[0, "rep_name"] = "Unmatched Analytical Rep"

    results = validator._validate_csv_paths(
        _export_tables(validator, modified, tmp_path)
    )

    assert all(result.passed for result in results)


def test_ddl_currency_constraint_rejects_non_usd_value(
    imperfect_tables: dict,
    tmp_path: Path,
) -> None:
    validator = SalesDuckDBFKValidator.for_profile("dev")
    malformed = _copy_tables(imperfect_tables)
    malformed["products"].loc[0, "currency_code"] = "EUR"

    checks = _results_by_name(
        validator._validate_csv_paths(_export_tables(validator, malformed, tmp_path))
    )

    assert not checks["products.duckdb_load"].passed


def test_invalid_canonical_ddl_is_reported(
    imperfect_tables: dict,
    tmp_path: Path,
) -> None:
    validator = SalesDuckDBFKValidator.for_profile("dev")
    ddl_path = tmp_path / "invalid.sql"
    ddl_path.write_text("CREATE TABLE broken (", encoding="utf-8")
    validator.settings = replace(validator.settings, schema_source=ddl_path)
    csv_dir = tmp_path / "csv"

    results = validator._validate_csv_paths(
        _export_tables(validator, imperfect_tables, csv_dir)
    )

    assert len(results) == 1
    assert not results[0].passed
    assert results[0].check_name == "schema.duckdb_ddl"


def test_exported_validation_reports_missing_csvs(tmp_path: Path) -> None:
    validator = SalesDuckDBFKValidator.for_profile("full")

    with pytest.raises(FileNotFoundError, match="leads.csv"):
        validator._csv_paths(tmp_path)


def test_validator_rejects_non_sales_settings() -> None:
    settings = GenerationSettings.from_config_files("crm", "dev")
    with pytest.raises(ValueError, match="only supports sales"):
        SalesDuckDBFKValidator(DeterministicGenerator(settings))


def _copy_tables(tables: dict) -> dict:
    return {name: table.copy(deep=True) for name, table in tables.items()}


def _export_tables(
    validator: SalesDuckDBFKValidator,
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
