"""DuckDB validation for required Sales analytical join paths."""

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
from generators.sales.config import load_sales_config
from generators.sales.config import settings_for_profile
from generators.sales.imperfections import SalesImperfectionInjector
from generators.sales.validators.config import validate_sales_config
from generators.sales.validators.relational import SalesRelationalValidator


REGISTERED_JOIN_SQL = {
    "sales_jp_005": """
        SELECT COUNT(*)
        FROM deals
        INNER JOIN quotations
            ON quotations.deal_id = deals.deal_id
        INNER JOIN products
            ON quotations.product_id = products.product_id
    """,
    "sales_jp_006": """
        SELECT COUNT(*)
        FROM leads
        INNER JOIN deals
            ON deals.lead_id = leads.lead_id
        INNER JOIN quotations
            ON quotations.deal_id = deals.deal_id
        INNER JOIN products
            ON quotations.product_id = products.product_id
    """,
    "sales_jp_007": """
        SELECT COUNT(*)
        FROM targets
        LEFT JOIN deals
            ON deals.rep_name = targets.rep_name
            AND deals.close_date >= targets.period_start
            AND deals.close_date < targets.period_end
            AND deals.stage = 'Won'
    """,
    "sales_jp_008": """
        SELECT COUNT(*)
        FROM targets
        INNER JOIN deals
            ON deals.rep_name = targets.rep_name
            AND deals.close_date >= targets.period_start
            AND deals.close_date < targets.period_end
            AND deals.stage = 'Won'
    """,
}


MANY_TO_MANY_CARDINALITY_SQL = {
    "quotations.deal_many_products": """
        SELECT COUNT(*)
        FROM (
            SELECT deal_id
            FROM quotations
            GROUP BY deal_id
            HAVING COUNT(DISTINCT product_id) > 1
        ) AS qualifying_deals
    """,
    "quotations.product_many_deals": """
        SELECT COUNT(*)
        FROM (
            SELECT product_id
            FROM quotations
            GROUP BY product_id
            HAVING COUNT(DISTINCT deal_id) > 1
        ) AS qualifying_products
    """,
}


class SalesJoinPathValidator:
    """Validate configured Sales INNER, LEFT, and analytical join paths."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "sales":
            raise ValueError("SalesJoinPathValidator only supports sales")
        validate_sales_config()
        self.generator = generator
        self.settings = generator.settings
        self.sales_config = load_sales_config()
        configured_registered_ids = {
            path["id"]
            for path in self.sales_config["join_path_requirements"]
            if len(path["tables"]) > 2 or "analytical" in path["join_type"]
        }
        if configured_registered_ids != set(REGISTERED_JOIN_SQL):
            raise ValueError(
                "Registered Sales join SQL differs from configured complex paths"
            )

    @classmethod
    def for_profile(cls, profile: str) -> "SalesJoinPathValidator":
        """Create a Sales join-path validator from validated config files."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def validate_exported_csvs(self) -> list[IntegrityCheckResult]:
        """Validate joins against CSVs in the configured output directory."""

        return self.validate_csv_directory(self.settings.output_path)

    def validate_csv_directory(
        self,
        directory: Path,
    ) -> list[IntegrityCheckResult]:
        """Validate join paths in one complete persisted Sales CSV directory."""

        return self._validate_csv_paths(self._csv_paths(directory))

    def generate_and_validate(self) -> list[IntegrityCheckResult]:
        """Generate final Sales data and validate joins through temporary CSVs."""

        tables = SalesImperfectionInjector(self.generator).generate_imperfect_tables()
        SalesRelationalValidator(self.generator).validate_or_raise(tables)
        with TemporaryDirectory(prefix="sales-join-validation-") as temp_dir:
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
            load_results = self._load_csvs(connection, csv_paths)
            results.extend(load_results)
            if any(not result.passed for result in load_results):
                return results

            for join_path in self.sales_config["join_path_requirements"]:
                results.extend(self._validate_join_path(connection, join_path))
            results.extend(self._validate_many_to_many_cardinality(connection))
        return results

    def _load_csvs(
        self,
        connection: Any,
        csv_paths: dict[str, Path],
    ) -> list[IntegrityCheckResult]:
        results: list[IntegrityCheckResult] = []
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
        return results

    def _validate_join_path(
        self,
        connection: Any,
        join_path: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        join_id = join_path["id"]
        joined_rows = _count(connection, _join_count_sql(join_path))
        results = [
            _positive_count_result(
                f"{join_id}.joined_rows",
                joined_rows,
                "join returned no rows",
            )
        ]
        if join_path["required_result"] == "non_empty_with_unmatched_parent_rows":
            unmatched_rows = _count(connection, _unmatched_count_sql(join_path))
            results.append(
                _positive_count_result(
                    f"{join_id}.unmatched_parent_rows",
                    unmatched_rows,
                    "LEFT JOIN path has no unmatched parent rows",
                )
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
                "Deal-to-Product many-to-many cardinality missing",
            )
            for check_name, sql in MANY_TO_MANY_CARDINALITY_SQL.items()
        ]


def _join_count_sql(join_path: dict[str, Any]) -> str:
    if join_path["id"] in REGISTERED_JOIN_SQL:
        return REGISTERED_JOIN_SQL[join_path["id"]]
    if len(join_path["tables"]) != 2:
        raise ValueError(f"No SQL registered for complex path {join_path['id']}")
    parent_table, child_table = join_path["tables"]
    join_keyword = "INNER JOIN" if join_path["join_type"] == "inner" else "LEFT JOIN"
    return f"""
        SELECT COUNT(*)
        FROM {parent_table}
        {join_keyword} {child_table}
            ON {join_path["join_condition"]}
    """


def _unmatched_count_sql(join_path: dict[str, Any]) -> str:
    if len(join_path["tables"]) != 2:
        raise ValueError("Unmatched-parent checks require a two-table path")
    parent_table, child_table = join_path["tables"]
    return f"""
        SELECT COUNT(*)
        FROM {parent_table}
        LEFT JOIN {child_table}
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
            "install requirements.txt before validating Sales join paths."
        ) from exc
    return duckdb


def _sql_string(path: Path) -> str:
    escaped = str(path).replace("'", "''")
    return f"'{escaped}'"


__all__ = [
    "MANY_TO_MANY_CARDINALITY_SQL",
    "REGISTERED_JOIN_SQL",
    "SalesJoinPathValidator",
]
