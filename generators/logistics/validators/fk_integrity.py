"""DuckDB DDL, physical FK, and declared-orphan validation for Logistics."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.csv_export import CSVExporter
from generators.core.imperfections import count_from_pct
from generators.core.integrity import IntegrityCheckResult
from generators.core.integrity import assert_all_passed
from generators.core.integrity import failed
from generators.core.integrity import passed
from generators.logistics.config import load_logistics_config
from generators.logistics.config import settings_for_profile
from generators.logistics.imperfections import LogisticsImperfectionInjector
from generators.logistics.validators.config import validate_logistics_config
from generators.logistics.validators.relational import LogisticsRelationalValidator


MANUAL_FK_CHECKS = {
    "shipments.order_id.fk": """
        SELECT COUNT(*)
        FROM shipments
        LEFT JOIN orders ON shipments.order_id = orders.order_id
        WHERE orders.order_id IS NULL
    """,
    "shipments.carrier_id.fk": """
        SELECT COUNT(*)
        FROM shipments
        LEFT JOIN carriers ON shipments.carrier_id = carriers.carrier_id
        WHERE carriers.carrier_id IS NULL
    """,
    "inventory.warehouse_id.fk": """
        SELECT COUNT(*)
        FROM inventory
        LEFT JOIN warehouses
            ON inventory.warehouse_id = warehouses.warehouse_id
        WHERE warehouses.warehouse_id IS NULL
    """,
}


class LogisticsDuckDBFKValidator:
    """Validate Logistics CSVs under DDL constraints and orphan policy."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "logistics":
            raise ValueError("LogisticsDuckDBFKValidator only supports logistics")
        validate_logistics_config()
        self.generator = generator
        self.settings = generator.settings
        self.config = load_logistics_config()

    @classmethod
    def for_profile(cls, profile: str) -> "LogisticsDuckDBFKValidator":
        """Create a DuckDB validator from validated Logistics config."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def validate_exported_csvs(self) -> list[IntegrityCheckResult]:
        """Validate CSVs from the configured profile output directory."""

        return self.validate_csv_directory(self.settings.output_path)

    def validate_csv_directory(
        self,
        directory: Path,
    ) -> list[IntegrityCheckResult]:
        """Validate one complete persisted Logistics CSV directory."""

        return self._validate_csv_paths(self._csv_paths(directory))

    def generate_and_validate(self) -> list[IntegrityCheckResult]:
        """Generate final tables and validate temporary persisted CSVs."""

        tables = LogisticsImperfectionInjector(
            self.generator
        ).generate_imperfect_tables()
        LogisticsRelationalValidator(self.generator).validate_or_raise(tables)
        with TemporaryDirectory(prefix="logistics-fk-validation-") as temp:
            directory = Path(temp)
            CSVExporter(self.settings.csv_format).export_tables(
                tables=tables,
                table_order=self.settings.table_order,
                output_dir=directory,
            )
            return self._validate_csv_paths(self._csv_paths(directory))

    def validate_exported_or_raise(self) -> list[IntegrityCheckResult]:
        """Validate configured exports and raise a combined error on failure."""

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
                "Logistics CSVs are missing. Export the requested profile first "
                "or use generated validation. Missing: "
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

            load_results = load_logistics_csvs(
                connection,
                csv_paths,
                self.settings.table_order,
            )
            results.extend(load_results)
            if any(not result.passed for result in load_results):
                return results

            results.extend(_execute_zero_count_checks(connection, MANUAL_FK_CHECKS))
            results.extend(self._validate_order_warehouse_exception(connection))
        return results

    def _validate_order_warehouse_exception(
        self,
        connection: Any,
    ) -> list[IntegrityCheckResult]:
        """Validate exact NULL/orphan counts and the declared ID formula."""

        target = self.config["imperfection_targets"]["orphaned_order_warehouses"]
        namespace = int(target["namespace_base"])
        expected_nulls = count_from_pct(
            self.generator.row_count("orders"),
            float(self.settings.imperfections["null_pct"]),
        )
        expected_orphans = count_from_pct(
            self.generator.row_count("orders"),
            float(target["rate_pct"]),
        )
        null_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM orders WHERE warehouse_id IS NULL"
            ).fetchone()[0]
        )
        orphan_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM orders
                LEFT JOIN warehouses
                    ON orders.warehouse_id = warehouses.warehouse_id
                WHERE orders.warehouse_id IS NOT NULL
                  AND warehouses.warehouse_id IS NULL
                """
            ).fetchone()[0]
        )
        formula_errors = int(
            connection.execute(
                f"""
                SELECT COUNT(*)
                FROM orders
                LEFT JOIN warehouses
                    ON orders.warehouse_id = warehouses.warehouse_id
                WHERE orders.warehouse_id IS NOT NULL
                  AND warehouses.warehouse_id IS NULL
                  AND orders.warehouse_id <> {namespace} + orders.order_id
                """
            ).fetchone()[0]
        )
        return [
            _exact_count_result(
                "orders.warehouse_id.null_count",
                null_count,
                expected_nulls,
            ),
            _exact_count_result(
                "orders.warehouse_id.declared_orphan_count",
                orphan_count,
                expected_orphans,
            ),
            _zero_count_result(
                "orders.warehouse_id.orphan_namespace",
                formula_errors,
            ),
        ]


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
        results.append(_zero_count_result(check_name, invalid_count))
    return results


def load_logistics_csvs(
    connection: Any,
    csv_paths: dict[str, Path],
    table_order: tuple[str, ...],
) -> list[IntegrityCheckResult]:
    """Load Logistics CSVs under DDL constraints in dependency-safe order."""

    results: list[IntegrityCheckResult] = []
    for table_name in table_order:
        try:
            connection.execute(_copy_sql(table_name, csv_paths[table_name]))
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
    return results


def _exact_count_result(
    check_name: str,
    actual: int,
    expected: int,
) -> IntegrityCheckResult:
    if actual == expected:
        return passed(check_name, f"exact controlled count {expected}")
    return failed(check_name, f"expected {expected}, got {actual}")


def _zero_count_result(
    check_name: str,
    invalid_count: int,
) -> IntegrityCheckResult:
    if invalid_count == 0:
        return passed(check_name, "zero invalid rows")
    return failed(check_name, f"{invalid_count} invalid row(s)")


def _copy_sql(table_name: str, path: Path) -> str:
    return f"""
        COPY {table_name}
        FROM {_sql_string(path)}
        (HEADER, DELIMITER ',', NULL '', DATEFORMAT '%Y-%m-%d',
         TIMESTAMPFORMAT '%Y-%m-%dT%H:%M:%S')
    """


def _require_duckdb() -> Any:
    try:
        import duckdb
    except ImportError as exc:
        raise ImportError(
            "Missing required dependency 'duckdb'. Create the root .venv and "
            "install requirements.txt before validating Logistics FK integrity."
        ) from exc
    return duckdb


def _sql_string(path: Path) -> str:
    escaped = str(path).replace("'", "''")
    return f"'{escaped}'"


__all__ = [
    "LogisticsDuckDBFKValidator",
    "MANUAL_FK_CHECKS",
    "load_logistics_csvs",
]
