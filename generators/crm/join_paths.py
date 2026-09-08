"""DuckDB validation for required CRM engagement join paths."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from generators.common.base import DeterministicGenerator
from generators.common.csv_export import CSVExporter
from generators.common.integrity import IntegrityCheckResult
from generators.common.integrity import assert_all_passed
from generators.common.integrity import failed
from generators.common.integrity import passed
from generators.crm.config import load_crm_config
from generators.crm.config import settings_for_profile
from generators.crm.config import validate_crm_config
from generators.crm.imperfections import CRMImperfectionInjector
from generators.crm.integrity import CRMRelationalValidator


THREE_TABLE_JOIN_SQL = {
    "crm_jp_003": """
        SELECT COUNT(*) AS joined_rows
        FROM accounts
        INNER JOIN contacts
            ON contacts.account_id = accounts.account_id
        INNER JOIN interactions
            ON interactions.contact_id = contacts.contact_id
    """,
    "crm_jp_004": """
        SELECT COUNT(*) AS joined_rows
        FROM contacts
        INNER JOIN contact_campaigns
            ON contact_campaigns.contact_id = contacts.contact_id
        INNER JOIN campaigns
            ON contact_campaigns.campaign_id = campaigns.campaign_id
    """,
    "crm_jp_009": """
        SELECT COUNT(*) AS joined_rows
        FROM accounts
        INNER JOIN support_cases
            ON support_cases.account_id = accounts.account_id
        INNER JOIN contacts
            ON support_cases.contact_id = contacts.contact_id
    """,
}


MANY_TO_MANY_CARDINALITY_SQL = {
    "contact_campaigns.contact_many_campaigns": """
        SELECT COUNT(*)
        FROM (
            SELECT contact_id
            FROM contact_campaigns
            GROUP BY contact_id
            HAVING COUNT(DISTINCT campaign_id) > 1
        ) AS qualifying_contacts
    """,
    "contact_campaigns.campaign_many_contacts": """
        SELECT COUNT(*)
        FROM (
            SELECT campaign_id
            FROM contact_campaigns
            GROUP BY campaign_id
            HAVING COUNT(DISTINCT contact_id) > 1
        ) AS qualifying_campaigns
    """,
}


class CRMJoinPathValidator:
    """Validate configured CRM INNER and LEFT join behavior in DuckDB."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "crm":
            raise ValueError("CRMJoinPathValidator only supports the crm domain")
        validate_crm_config()
        self.generator = generator
        self.settings = generator.settings
        self.crm_config = load_crm_config()
        configured_three_table_ids = {
            path["id"]
            for path in self.crm_config["join_path_requirements"]
            if len(path["tables"]) == 3
        }
        if configured_three_table_ids != set(THREE_TABLE_JOIN_SQL):
            raise ValueError("Three-table join SQL differs from configured join paths")

    @classmethod
    def for_profile(cls, profile: str) -> "CRMJoinPathValidator":
        """Create a CRM join-path validator from validated config files."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def validate_exported_csvs(self) -> list[IntegrityCheckResult]:
        """Validate joins against CSVs in the configured output path."""

        return self._validate_csv_paths(self._csv_paths(self.settings.output_path))

    def generate_and_validate(self) -> list[IntegrityCheckResult]:
        """Generate imperfect tables and validate joins through temporary CSVs."""

        tables = CRMImperfectionInjector(self.generator).generate_imperfect_tables()
        relational_results = CRMRelationalValidator(self.generator).validate_tables(
            tables
        )
        assert_all_passed(relational_results)

        with TemporaryDirectory(prefix="crm-join-validation-") as temp_dir:
            temp_path = Path(temp_dir)
            CSVExporter(self.settings.csv_format).export_tables(
                tables=tables,
                table_order=self.settings.table_order,
                output_dir=temp_path,
            )
            return self._validate_csv_paths(self._csv_paths(temp_path))

    def validate_exported_or_raise(self) -> list[IntegrityCheckResult]:
        """Validate exported CSV joins and raise a combined error on failure."""

        results = self.validate_exported_csvs()
        assert_all_passed(results)
        return results

    def _csv_paths(self, directory: Path) -> dict[str, Path]:
        csv_paths = {
            table_name: directory / f"{table_name}.csv"
            for table_name in self.settings.table_order
        }
        missing = [path for path in csv_paths.values() if not path.exists()]
        if missing:
            missing_text = ", ".join(str(path) for path in missing)
            raise FileNotFoundError(
                "CRM CSVs are missing. Run "
                "`python main.py export-csvs --profile full` first, or use "
                "`--generated` for temporary generated CSV validation. Missing: "
                f"{missing_text}"
            )
        return csv_paths

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

            for join_path in self.crm_config["join_path_requirements"]:
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
                        "failed to load CSV into constrained table: " f"{exc}",
                    )
                )
                return results
            results.append(
                passed(
                    f"{table_name}.duckdb_load",
                    "loaded under DDL constraints",
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
                "many-to-many cardinality missing",
            )
            for check_name, sql in MANY_TO_MANY_CARDINALITY_SQL.items()
        ]


def _join_count_sql(join_path: dict[str, Any]) -> str:
    if len(join_path["tables"]) == 3:
        try:
            return THREE_TABLE_JOIN_SQL[join_path["id"]]
        except KeyError as exc:
            raise ValueError(
                f"No SQL registered for three-table path {join_path['id']}"
            ) from exc

    parent_table, child_table = join_path["tables"]
    join_keyword = "INNER JOIN" if join_path["join_type"] == "inner" else "LEFT JOIN"
    return f"""
        SELECT COUNT(*) AS joined_rows
        FROM {parent_table}
        {join_keyword} {child_table}
            ON {join_path["join_condition"]}
    """


def _unmatched_count_sql(join_path: dict[str, Any]) -> str:
    if len(join_path["tables"]) != 2:
        raise ValueError("Unmatched-parent checks only support two-table paths")
    parent_table, child_table = join_path["tables"]
    return f"""
        SELECT COUNT(*) AS unmatched_rows
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
            "install requirements.txt before validating join paths."
        ) from exc
    return duckdb


def _sql_string(path: Path) -> str:
    escaped = str(path).replace("'", "''")
    return f"'{escaped}'"
