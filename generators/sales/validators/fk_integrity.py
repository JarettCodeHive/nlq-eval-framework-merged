"""DuckDB DDL and foreign-key validation for Sales CSVs."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.csv_export import CSVExporter
from generators.core.integrity import IntegrityCheckResult
from generators.core.integrity import assert_all_passed
from generators.core.integrity import failed
from generators.core.integrity import passed
from generators.sales.config import settings_for_profile
from generators.sales.imperfections import SalesImperfectionInjector
from generators.sales.validators.config import validate_sales_config
from generators.sales.validators.relational import SalesRelationalValidator


MANUAL_FK_CHECKS = {
    "deals.lead_id.fk": """
        SELECT COUNT(*)
        FROM deals
        LEFT JOIN leads
            ON deals.lead_id = leads.lead_id
        WHERE leads.lead_id IS NULL
    """,
    "quotations.deal_id.fk": """
        SELECT COUNT(*)
        FROM quotations
        LEFT JOIN deals
            ON quotations.deal_id = deals.deal_id
        WHERE deals.deal_id IS NULL
    """,
    "quotations.product_id.fk": """
        SELECT COUNT(*)
        FROM quotations
        LEFT JOIN products
            ON quotations.product_id = products.product_id
        WHERE products.product_id IS NULL
    """,
}


class SalesDuckDBFKValidator:
    """Validate Sales CSVs using constrained loads and explicit anti-joins."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "sales":
            raise ValueError("SalesDuckDBFKValidator only supports sales")
        validate_sales_config()
        self.generator = generator
        self.settings = generator.settings

    @classmethod
    def for_profile(cls, profile: str) -> "SalesDuckDBFKValidator":
        """Create a DuckDB validator from validated Sales config files."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def validate_exported_csvs(self) -> list[IntegrityCheckResult]:
        """Validate CSVs from the configured profile output directory."""

        return self.validate_csv_directory(self.settings.output_path)

    def validate_csv_directory(
        self,
        directory: Path,
    ) -> list[IntegrityCheckResult]:
        """Validate one complete persisted Sales CSV directory."""

        return self._validate_csv_paths(self._csv_paths(directory))

    def generate_and_validate(self) -> list[IntegrityCheckResult]:
        """Generate final Sales tables and validate temporary CSV exports."""

        tables = SalesImperfectionInjector(self.generator).generate_imperfect_tables()
        SalesRelationalValidator(self.generator).validate_or_raise(tables)
        with TemporaryDirectory(prefix="sales-fk-validation-") as temp_dir:
            directory = Path(temp_dir)
            CSVExporter(self.settings.csv_format).export_tables(
                tables=tables,
                table_order=self.settings.table_order,
                output_dir=directory,
            )
            return self._validate_csv_paths(self._csv_paths(directory))

    def validate_exported_or_raise(self) -> list[IntegrityCheckResult]:
        """Validate configured exports and raise one combined error on failure."""

        results = self.validate_exported_csvs()
        assert_all_passed(results)
        return results

    def _csv_paths(self, directory: Path) -> dict[str, Path]:
        paths = {
            table_name: directory / f"{table_name}.csv"
            for table_name in self.settings.table_order
        }
        missing = [path for path in paths.values() if not path.exists()]
        if missing:
            raise FileNotFoundError(
                "Sales CSVs are missing. Export the requested profile first or "
                "use generated validation. Missing: "
                + ", ".join(str(path) for path in missing)
            )
        return paths

    def _validate_csv_paths(
        self,
        csv_paths: dict[str, Path],
    ) -> list[IntegrityCheckResult]:
        duckdb = _require_duckdb()
        ddl = self.settings.schema_source.read_text(encoding="utf-8")
        results: list[IntegrityCheckResult] = []

        with duckdb.connect(database=":memory:") as connection:
            try:
                connection.execute(ddl)
            except Exception as exc:
                return [
                    failed(
                        "schema.duckdb_ddl",
                        f"failed to execute canonical DDL: {exc}",
                    )
                ]
            results.append(
                passed("schema.duckdb_ddl", "canonical DDL executed successfully")
            )

            # Dependency order ensures every parent exists before child COPY.
            for table_name in self.settings.table_order:
                try:
                    connection.execute(
                        f"""
                        COPY {table_name}
                        FROM {_sql_string(csv_paths[table_name])}
                        (HEADER, DELIMITER ',', NULL '', DATEFORMAT '%Y-%m-%d',
                         TIMESTAMPFORMAT '%Y-%m-%dT%H:%M:%S')
                        """
                    )
                except Exception as exc:
                    results.append(
                        failed(
                            f"{table_name}.duckdb_load",
                            f"failed to load CSV into constrained table: {exc}",
                        )
                    )
                    return results
                results.append(
                    passed(
                        f"{table_name}.duckdb_load",
                        "loaded under canonical DDL constraints",
                    )
                )

            results.extend(_execute_zero_count_checks(connection, MANUAL_FK_CHECKS))
        return results


def _execute_zero_count_checks(
    connection: Any,
    checks: dict[str, str],
) -> list[IntegrityCheckResult]:
    results: list[IntegrityCheckResult] = []
    for check_name, sql in checks.items():
        try:
            invalid_count = int(connection.execute(sql).fetchone()[0])
        except Exception as exc:
            results.append(failed(check_name, f"check query failed: {exc}"))
            continue
        if invalid_count == 0:
            results.append(passed(check_name, "zero orphan rows"))
        else:
            results.append(failed(check_name, f"{invalid_count} orphan row(s)"))
    return results


def _require_duckdb() -> Any:
    try:
        import duckdb
    except ImportError as exc:
        raise ImportError(
            "Missing required dependency 'duckdb'. Create the root .venv and "
            "install requirements.txt before validating Sales FK integrity."
        ) from exc
    return duckdb


def _sql_string(path: Path) -> str:
    escaped = str(path).replace("'", "''")
    return f"'{escaped}'"


__all__ = ["MANUAL_FK_CHECKS", "SalesDuckDBFKValidator"]
