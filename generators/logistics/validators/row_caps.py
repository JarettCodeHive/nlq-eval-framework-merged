"""Logistics configured, generated, and exported row-cap validation."""

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
from generators.logistics.config import settings_for_profile
from generators.logistics.imperfections import LogisticsImperfectionInjector
from generators.logistics.validators.config import validate_logistics_config


class LogisticsRowCapValidator:
    """Validate final Logistics row counts against the per-table hard cap."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "logistics":
            raise ValueError("LogisticsRowCapValidator only supports logistics")
        validate_logistics_config()
        self.generator = generator
        self.settings = generator.settings

    @classmethod
    def for_profile(cls, profile: str) -> "LogisticsRowCapValidator":
        """Create a row-cap validator from validated Logistics config."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def expected_final_row_counts(self) -> dict[str, int]:
        """Return expected rows after duplicate shipment injection."""

        row_counts = {
            table_name: self.generator.row_count(table_name)
            for table_name in self.settings.table_order
        }
        row_counts["shipments"] += count_from_pct(
            row_counts["shipments"],
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
        """Generate final Logistics tables and validate their row counts."""

        tables = LogisticsImperfectionInjector(
            self.generator
        ).generate_imperfect_tables()
        return self.validate_tables(tables)

    def validate_tables(self, tables: dict[str, Any]) -> list[IntegrityCheckResult]:
        """Validate in-memory Logistics table lengths against the hard cap."""

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
        """Validate row counts for one complete Logistics CSV directory."""

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
        """Raise when an in-memory Logistics table exceeds the hard cap."""

        results = self.validate_tables(tables)
        assert_all_passed(results)
        return results

    def validate_exported_or_raise(self) -> list[IntegrityCheckResult]:
        """Raise when an exported Logistics CSV exceeds the hard cap."""

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
                "Logistics CSVs are missing. Generate or export the requested "
                "profile first. Missing: " + ", ".join(str(path) for path in missing)
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


__all__ = ["LogisticsRowCapValidator"]
