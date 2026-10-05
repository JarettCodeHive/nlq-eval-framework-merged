"""DuckDB validation for required Logistics analytical join paths."""

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
from generators.logistics.config import load_logistics_config
from generators.logistics.config import settings_for_profile
from generators.logistics.imperfections import LogisticsImperfectionInjector
from generators.logistics.validators.config import validate_logistics_config
from generators.logistics.validators.fk_integrity import load_logistics_csvs
from generators.logistics.validators.relational import LogisticsRelationalValidator


REGISTERED_JOIN_SQL = {
    "logistics_jp_006": """
        SELECT COUNT(*)
        FROM orders
        INNER JOIN shipments
            ON orders.order_id = shipments.order_id
        INNER JOIN carriers
            ON shipments.carrier_id = carriers.carrier_id
    """,
}


WAREHOUSE_UNMATCHED_SQL = {
    "logistics_jp_004.null_unmatched_rows": """
        SELECT COUNT(*)
        FROM orders
        LEFT JOIN warehouses
            ON orders.warehouse_id = warehouses.warehouse_id
        WHERE warehouses.warehouse_id IS NULL
          AND orders.warehouse_id IS NULL
    """,
    "logistics_jp_004.orphan_unmatched_rows": """
        SELECT COUNT(*)
        FROM orders
        LEFT JOIN warehouses
            ON orders.warehouse_id = warehouses.warehouse_id
        WHERE warehouses.warehouse_id IS NULL
          AND orders.warehouse_id IS NOT NULL
    """,
}


MANY_TO_MANY_CARDINALITY_SQL = {
    "shipments.order_many_carriers": """
        SELECT COUNT(*)
        FROM (
            SELECT order_id
            FROM shipments
            GROUP BY order_id
            HAVING COUNT(DISTINCT carrier_id) > 1
        ) AS qualifying_orders
    """,
    "shipments.carrier_many_orders": """
        SELECT COUNT(*)
        FROM (
            SELECT carrier_id
            FROM shipments
            GROUP BY carrier_id
            HAVING COUNT(DISTINCT order_id) > 1
        ) AS qualifying_carriers
    """,
}


class LogisticsJoinPathValidator:
    """Validate configured INNER, LEFT, three-table, and M:N paths."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "logistics":
            raise ValueError("LogisticsJoinPathValidator only supports logistics")
        validate_logistics_config()
        self.generator = generator
        self.settings = generator.settings
        self.config = load_logistics_config()
        complex_ids = {
            path["id"]
            for path in self.config["join_path_requirements"]
            if len(path["tables"]) > 2
        }
        if complex_ids != set(REGISTERED_JOIN_SQL):
            raise ValueError(
                "Registered Logistics join SQL differs from configured complex paths"
            )

    @classmethod
    def for_profile(cls, profile: str) -> "LogisticsJoinPathValidator":
        """Create a join-path validator from validated Logistics config."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def validate_exported_csvs(self) -> list[IntegrityCheckResult]:
        """Validate joins against CSVs in the configured output directory."""

        return self.validate_csv_directory(self.settings.output_path)

    def validate_csv_directory(
        self,
        directory: Path,
    ) -> list[IntegrityCheckResult]:
        """Validate join paths in one complete persisted CSV directory."""

        return self._validate_csv_paths(self._csv_paths(directory))

    def generate_and_validate(self) -> list[IntegrityCheckResult]:
        """Generate final data and validate joins through temporary CSVs."""

        tables = LogisticsImperfectionInjector(
            self.generator
        ).generate_imperfect_tables()
        LogisticsRelationalValidator(self.generator).validate_or_raise(tables)
        with TemporaryDirectory(prefix="logistics-join-validation-") as temp:
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

            for join_path in self.config["join_path_requirements"]:
                results.extend(self._validate_join_path(connection, join_path))
            results.extend(self._validate_many_to_many_cardinality(connection))
        return results

    def _validate_join_path(
        self,
        connection: Any,
        join_path: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        join_id = join_path["id"]
        results = [
            _positive_count_result(
                f"{join_id}.joined_rows",
                _count(connection, _join_count_sql(join_path)),
                "join returned no rows",
            )
        ]
        if join_path["required_result"] == "non_empty_with_unmatched_parent_rows":
            results.append(
                _positive_count_result(
                    f"{join_id}.unmatched_parent_rows",
                    _count(connection, _unmatched_count_sql(join_path)),
                    "LEFT JOIN path has no unmatched order rows",
                )
            )
            results.extend(
                _positive_count_result(
                    check_name,
                    _count(connection, sql),
                    "required unmatched warehouse case is absent",
                )
                for check_name, sql in WAREHOUSE_UNMATCHED_SQL.items()
            )
        return results

    def _validate_many_to_many_cardinality(
        self,
        connection: Any,
    ) -> list[IntegrityCheckResult]:
        return [
            _positive_count_result(
                check_name,
                _count(connection, sql),
                "Order-to-Carrier many-to-many cardinality is missing",
            )
            for check_name, sql in MANY_TO_MANY_CARDINALITY_SQL.items()
        ]


def _join_count_sql(join_path: dict[str, Any]) -> str:
    if join_path["id"] in REGISTERED_JOIN_SQL:
        return REGISTERED_JOIN_SQL[join_path["id"]]
    if len(join_path["tables"]) != 2:
        raise ValueError(f"No SQL registered for complex path {join_path['id']}")
    left_table, right_table = join_path["tables"]
    keyword = (
        "LEFT JOIN" if join_path["join_type"] == "left_analytical" else "INNER JOIN"
    )
    return f"""
        SELECT COUNT(*)
        FROM {left_table}
        {keyword} {right_table}
            ON {join_path["join_condition"]}
    """


def _unmatched_count_sql(join_path: dict[str, Any]) -> str:
    left_table, right_table = join_path["tables"]
    return f"""
        SELECT COUNT(*)
        FROM {left_table}
        LEFT JOIN {right_table}
            ON {join_path["join_condition"]}
        WHERE {join_path["unmatched_condition"]}
    """


def _positive_count_result(
    check_name: str,
    count: int,
    zero_message: str,
) -> IntegrityCheckResult:
    if count > 0:
        return passed(check_name, f"{count} qualifying row(s)")
    return failed(check_name, zero_message)


def _count(connection: Any, sql: str) -> int:
    return int(connection.execute(sql).fetchone()[0])


def _require_duckdb() -> Any:
    try:
        import duckdb
    except ImportError as exc:
        raise ImportError(
            "Missing required dependency 'duckdb'. Create the root .venv and "
            "install requirements.txt before validating Logistics join paths."
        ) from exc
    return duckdb


__all__ = [
    "LogisticsJoinPathValidator",
    "MANY_TO_MANY_CARDINALITY_SQL",
    "REGISTERED_JOIN_SQL",
    "WAREHOUSE_UNMATCHED_SQL",
]
