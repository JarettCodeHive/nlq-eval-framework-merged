"""DuckDB DDL and foreign-key validation for Project Management CSVs."""

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
from generators.project_management.config import settings_for_profile
from generators.project_management.imperfections import (
    ProjectManagementImperfectionInjector,
)
from generators.project_management.validators.config import (
    validate_project_management_config,
)
from generators.project_management.validators.relational import (
    ProjectManagementRelationalValidator,
)


MANUAL_FK_CHECKS = {
    "tasks.project_id.fk": """
        SELECT COUNT(*)
        FROM tasks
        LEFT JOIN projects ON tasks.project_id = projects.project_id
        WHERE projects.project_id IS NULL
    """,
    "task_resources.task_id.fk": """
        SELECT COUNT(*)
        FROM task_resources
        LEFT JOIN tasks ON task_resources.task_id = tasks.task_id
        WHERE tasks.task_id IS NULL
    """,
    "task_resources.resource_id.fk": """
        SELECT COUNT(*)
        FROM task_resources
        LEFT JOIN resources
            ON task_resources.resource_id = resources.resource_id
        WHERE resources.resource_id IS NULL
    """,
    "milestones.project_id.fk": """
        SELECT COUNT(*)
        FROM milestones
        LEFT JOIN projects ON milestones.project_id = projects.project_id
        WHERE projects.project_id IS NULL
    """,
    "time_entries.task_id.fk": """
        SELECT COUNT(*)
        FROM time_entries
        LEFT JOIN tasks ON time_entries.task_id = tasks.task_id
        WHERE tasks.task_id IS NULL
    """,
    "time_entries.resource_id.fk": """
        SELECT COUNT(*)
        FROM time_entries
        LEFT JOIN resources ON time_entries.resource_id = resources.resource_id
        WHERE resources.resource_id IS NULL
    """,
}


SEMANTIC_RELATIONSHIP_CHECKS = {
    "time_entries.task_resource_membership": """
        SELECT COUNT(*)
        FROM time_entries
        LEFT JOIN task_resources
            ON time_entries.task_id = task_resources.task_id
           AND time_entries.resource_id = task_resources.resource_id
        WHERE task_resources.task_id IS NULL
    """,
}


class ProjectManagementDuckDBFKValidator:
    """Validate PM CSVs using canonical DDL and explicit anti-joins."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "project_management":
            raise ValueError(
                "ProjectManagementDuckDBFKValidator only supports "
                "project_management"
            )
        validate_project_management_config()
        self.generator = generator
        self.settings = generator.settings

    @classmethod
    def for_profile(cls, profile: str) -> "ProjectManagementDuckDBFKValidator":
        """Create a DuckDB validator from validated PM configuration."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def validate_exported_csvs(self) -> list[IntegrityCheckResult]:
        """Validate CSVs from the configured profile output directory."""

        return self.validate_csv_directory(self.settings.output_path)

    def validate_csv_directory(
        self,
        directory: Path,
    ) -> list[IntegrityCheckResult]:
        """Validate one complete persisted PM CSV directory."""

        return self._validate_csv_paths(self._csv_paths(directory))

    def generate_and_validate(self) -> list[IntegrityCheckResult]:
        """Generate final PM tables and validate temporary CSV exports."""

        tables = ProjectManagementImperfectionInjector(
            self.generator
        ).generate_imperfect_tables()
        ProjectManagementRelationalValidator(self.generator).validate_or_raise(tables)
        with TemporaryDirectory(prefix="project-management-fk-validation-") as temp:
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

            results.extend(_execute_zero_count_checks(connection, MANUAL_FK_CHECKS))
            results.extend(
                _execute_zero_count_checks(connection, SEMANTIC_RELATIONSHIP_CHECKS)
            )
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
            results.append(passed(check_name, "zero invalid rows"))
        else:
            results.append(failed(check_name, f"{invalid_count} invalid row(s)"))
    return results


def load_project_management_csvs(
    connection: Any,
    csv_paths: dict[str, Path],
    table_order: tuple[str, ...],
) -> list[IntegrityCheckResult]:
    """Load PM CSVs under DDL constraints in dependency-safe order."""

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
            "install requirements.txt before validating PM FK integrity."
        ) from exc
    return duckdb


def _sql_string(path: Path) -> str:
    escaped = str(path).replace("'", "''")
    return f"'{escaped}'"


__all__ = [
    "MANUAL_FK_CHECKS",
    "ProjectManagementDuckDBFKValidator",
    "SEMANTIC_RELATIONSHIP_CHECKS",
    "load_project_management_csvs",
]
