"""DuckDB validation for Project Management analytical join paths."""

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
from generators.project_management.config import load_project_management_config
from generators.project_management.config import settings_for_profile
from generators.project_management.imperfections import (
    ProjectManagementImperfectionInjector,
)
from generators.project_management.validators.config import (
    validate_project_management_config,
)
from generators.project_management.validators.fk_integrity import (
    load_project_management_csvs,
)
from generators.project_management.validators.relational import (
    ProjectManagementRelationalValidator,
)


REGISTERED_JOIN_SQL = {
    "pm_jp_005": """
        SELECT COUNT(*)
        FROM tasks
        INNER JOIN task_resources
            ON tasks.task_id = task_resources.task_id
        INNER JOIN resources
            ON task_resources.resource_id = resources.resource_id
    """,
    "pm_jp_009": """
        SELECT COUNT(*)
        FROM projects
        INNER JOIN tasks ON projects.project_id = tasks.project_id
        INNER JOIN time_entries ON tasks.task_id = time_entries.task_id
    """,
    "pm_jp_010": """
        SELECT COUNT(*)
        FROM time_entries
        INNER JOIN task_resources
            ON time_entries.task_id = task_resources.task_id
           AND time_entries.resource_id = task_resources.resource_id
    """,
}


COMPLETE_MEMBERSHIP_SQL = """
    SELECT COUNT(*)
    FROM time_entries
    LEFT JOIN task_resources
        ON time_entries.task_id = task_resources.task_id
       AND time_entries.resource_id = task_resources.resource_id
    WHERE task_resources.task_id IS NULL
"""


MANY_TO_MANY_CARDINALITY_SQL = {
    "task_resources.task_many_resources": """
        SELECT COUNT(*)
        FROM (
            SELECT task_id
            FROM task_resources
            GROUP BY task_id
            HAVING COUNT(DISTINCT resource_id) > 1
        ) AS qualifying_tasks
    """,
    "task_resources.resource_many_tasks": """
        SELECT COUNT(*)
        FROM (
            SELECT resource_id
            FROM task_resources
            GROUP BY resource_id
            HAVING COUNT(DISTINCT task_id) > 1
        ) AS qualifying_resources
    """,
}


class ProjectManagementJoinPathValidator:
    """Validate configured PM INNER, LEFT, and semantic join behavior."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "project_management":
            raise ValueError(
                "ProjectManagementJoinPathValidator only supports " "project_management"
            )
        validate_project_management_config()
        self.generator = generator
        self.settings = generator.settings
        self.pm_config = load_project_management_config()
        configured_complex_ids = {
            path["id"]
            for path in self.pm_config["join_path_requirements"]
            if len(path["tables"]) > 2
            or path["join_type"] == "inner_semantic_composite"
        }
        if configured_complex_ids != set(REGISTERED_JOIN_SQL):
            raise ValueError(
                "Registered PM join SQL differs from configured complex paths"
            )

    @classmethod
    def for_profile(cls, profile: str) -> "ProjectManagementJoinPathValidator":
        """Create a PM join-path validator from validated configuration."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def validate_exported_csvs(self) -> list[IntegrityCheckResult]:
        """Validate joins against CSVs in the configured output directory."""

        return self.validate_csv_directory(self.settings.output_path)

    def validate_csv_directory(
        self,
        directory: Path,
    ) -> list[IntegrityCheckResult]:
        """Validate join paths in one complete persisted PM CSV directory."""

        return self._validate_csv_paths(self._csv_paths(directory))

    def generate_and_validate(self) -> list[IntegrityCheckResult]:
        """Generate final PM data and validate joins through temporary CSVs."""

        tables = ProjectManagementImperfectionInjector(
            self.generator
        ).generate_imperfect_tables()
        ProjectManagementRelationalValidator(self.generator).validate_or_raise(tables)
        with TemporaryDirectory(prefix="project-management-join-validation-") as temp:
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
                "Project Management CSVs are missing. Export the requested "
                "profile first or use generated validation. Missing: "
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
            load_results = load_project_management_csvs(
                connection,
                csv_paths,
                self.settings.table_order,
            )
            results.extend(load_results)
            if any(not result.passed for result in load_results):
                return results

            for join_path in self.pm_config["join_path_requirements"]:
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
                    "LEFT JOIN path has no unmatched parent rows",
                )
            )
        if join_path["required_result"] == "complete_membership":
            results.append(
                _zero_count_result(
                    f"{join_id}.invalid_memberships",
                    _count(connection, COMPLETE_MEMBERSHIP_SQL),
                    "one or more time entries lack a declared assignment",
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
                "Task-to-Resource many-to-many cardinality missing",
            )
            for check_name, sql in MANY_TO_MANY_CARDINALITY_SQL.items()
        ]


def _join_count_sql(join_path: dict[str, Any]) -> str:
    if join_path["id"] in REGISTERED_JOIN_SQL:
        return REGISTERED_JOIN_SQL[join_path["id"]]
    if len(join_path["tables"]) != 2:
        raise ValueError(f"No SQL registered for complex path {join_path['id']}")
    parent_table, child_table = join_path["tables"]
    keyword = "INNER JOIN" if join_path["join_type"] == "inner" else "LEFT JOIN"
    return f"""
        SELECT COUNT(*)
        FROM {parent_table}
        {keyword} {child_table}
            ON {join_path["join_condition"]}
    """


def _unmatched_count_sql(join_path: dict[str, Any]) -> str:
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
    return (
        passed(check_name, f"{count} qualifying row(s)")
        if count > 0
        else failed(check_name, zero_message)
    )


def _zero_count_result(
    check_name: str,
    count: int,
    nonzero_message: str,
) -> IntegrityCheckResult:
    return (
        passed(check_name, "zero invalid rows")
        if count == 0
        else failed(check_name, f"{nonzero_message}: {count}")
    )


def _count(connection: Any, sql: str) -> int:
    return int(connection.execute(sql).fetchone()[0])


def _require_duckdb() -> Any:
    try:
        import duckdb
    except ImportError as exc:
        raise ImportError(
            "Missing required dependency 'duckdb'. Create the root .venv and "
            "install requirements.txt before validating PM join paths."
        ) from exc
    return duckdb


__all__ = [
    "MANY_TO_MANY_CARDINALITY_SQL",
    "ProjectManagementJoinPathValidator",
    "REGISTERED_JOIN_SQL",
]
