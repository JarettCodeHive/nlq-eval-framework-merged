"""Project Management configured, generated, and exported row-cap validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.csv_export import count_csv_rows
from generators.core.imperfections import count_from_pct
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


class ProjectManagementRowCapValidator:
    """Validate final PM row counts against the shared per-table hard cap."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "project_management":
            raise ValueError(
                "ProjectManagementRowCapValidator only supports " "project_management"
            )
        validate_project_management_config()
        self.generator = generator
        self.settings = generator.settings

    @classmethod
    def for_profile(cls, profile: str) -> "ProjectManagementRowCapValidator":
        """Create a PM row-cap validator from validated configuration."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def expected_final_row_counts(self) -> dict[str, int]:
        """Return expected rows after near-duplicate time-entry injection."""

        row_counts = {
            table_name: self.generator.row_count(table_name)
            for table_name in self.settings.table_order
        }
        row_counts["time_entries"] += count_from_pct(
            row_counts["time_entries"],
            float(self.settings.imperfections["duplicate_pct"]),
        )
        return row_counts

    def validate_expected_counts(self) -> list[IntegrityCheckResult]:
        """Validate configured final expected counts against the hard cap."""

        return self._validate_counts(
            self.expected_final_row_counts(),
            source="expected",
        )

    def generate_and_validate(self) -> list[IntegrityCheckResult]:
        """Generate final imperfect PM tables and validate actual row counts."""

        tables = ProjectManagementImperfectionInjector(
            self.generator
        ).generate_imperfect_tables()
        return self.validate_tables(tables)

    def validate_tables(self, tables: dict[str, Any]) -> list[IntegrityCheckResult]:
        """Validate generated in-memory table lengths against the hard cap."""

        counts = {
            table_name: len(tables[table_name])
            for table_name in self.settings.table_order
        }
        return self._validate_counts(counts, source="actual")

    def validate_exported_csvs(self) -> list[IntegrityCheckResult]:
        """Validate CSV counts from the configured profile output directory."""

        return self.validate_csv_directory(self.settings.output_path)

    def validate_csv_directory(
        self,
        directory: Path,
    ) -> list[IntegrityCheckResult]:
        """Validate row counts for one complete PM CSV directory."""

        csv_paths = self._csv_paths(directory)
        counts = {
            table_name: count_csv_rows(csv_paths[table_name])
            for table_name in self.settings.table_order
        }
        return self._validate_counts(counts, source="exported")

    def validate_expected_or_raise(self) -> list[IntegrityCheckResult]:
        """Raise when a configured final count exceeds the hard cap."""

        results = self.validate_expected_counts()
        assert_all_passed(results)
        return results

    def validate_generated_or_raise(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        """Raise when an in-memory PM table exceeds the hard cap."""

        results = self.validate_tables(tables)
        assert_all_passed(results)
        return results

    def validate_exported_or_raise(self) -> list[IntegrityCheckResult]:
        """Raise when an exported PM CSV exceeds the hard cap."""

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
                "Project Management CSVs are missing. Generate or export the "
                "requested profile first. Missing: "
                + ", ".join(str(path) for path in missing)
            )
        return paths

    def _validate_counts(
        self,
        row_counts: dict[str, int],
        source: str,
    ) -> list[IntegrityCheckResult]:
        cap = self.settings.max_rows_per_table
        results: list[IntegrityCheckResult] = []
        for table_name in self.settings.table_order:
            row_count = row_counts[table_name]
            check_name = f"{table_name}.row_cap.{source}"
            if row_count <= cap:
                results.append(passed(check_name, f"{row_count} rows <= cap {cap}"))
            else:
                results.append(
                    failed(check_name, f"{row_count} rows exceeds cap {cap}")
                )
        return results


__all__ = ["ProjectManagementRowCapValidator"]
