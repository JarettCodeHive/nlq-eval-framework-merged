"""CRM row-cap validation."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.imperfections import count_from_pct
from generators.core.integrity import IntegrityCheckResult
from generators.core.integrity import assert_all_passed
from generators.core.integrity import failed
from generators.core.integrity import passed
from generators.crm.config import settings_for_profile
from generators.crm.validators.config import validate_crm_config
from generators.crm.imperfections import CRMImperfectionInjector


class CRMRowCapValidator:
    """Validate CRM configured and generated row counts against the hard cap."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "crm":
            raise ValueError("CRMRowCapValidator only supports the crm domain")
        validate_crm_config()
        self.generator = generator
        self.settings = generator.settings

    @classmethod
    def for_profile(cls, profile: str) -> "CRMRowCapValidator":
        """Create a CRM row-cap validator from config files."""

        settings = settings_for_profile(profile)
        return cls(DeterministicGenerator(settings))

    def expected_final_row_counts(self) -> dict[str, int]:
        """Return expected final row counts after configured imperfections."""

        row_counts = {
            table_name: self.generator.row_count(table_name)
            for table_name in self.settings.table_order
        }
        row_counts["contacts"] += count_from_pct(
            self.generator.row_count("contacts"),
            float(self.settings.imperfections["duplicate_pct"]),
        )
        return row_counts

    def validate_expected_counts(self) -> list[IntegrityCheckResult]:
        """Validate configured final expected row counts against the cap."""

        return self._validate_counts(
            self.expected_final_row_counts(), source="expected"
        )

    def generate_and_validate(self) -> list[IntegrityCheckResult]:
        """Generate imperfect CRM tables and validate actual row counts."""

        tables = CRMImperfectionInjector(self.generator).generate_imperfect_tables()
        return self.validate_tables(tables)

    def validate_exported_csvs(self) -> list[IntegrityCheckResult]:
        """Validate row counts read from the configured exported CSV files."""

        return self.validate_csv_directory(self.settings.output_path)

    def validate_csv_directory(
        self,
        directory: Path,
    ) -> list[IntegrityCheckResult]:
        """Validate row counts for a complete CRM CSV directory."""

        csv_paths = self._csv_paths(directory)
        row_counts = {
            table_name: _csv_row_count(csv_paths[table_name])
            for table_name in self.settings.table_order
        }
        return self._validate_counts(row_counts, source="exported")

    def validate_tables(self, tables: dict[str, Any]) -> list[IntegrityCheckResult]:
        """Validate actual generated table lengths against the cap."""

        actual_counts = {
            table_name: len(tables[table_name])
            for table_name in self.settings.table_order
        }
        return self._validate_counts(actual_counts, source="actual")

    def validate_expected_or_raise(self) -> list[IntegrityCheckResult]:
        """Validate configured counts and raise if any table exceeds the cap."""

        results = self.validate_expected_counts()
        assert_all_passed(results)
        return results

    def validate_exported_or_raise(self) -> list[IntegrityCheckResult]:
        """Validate exported CSV counts and raise if a table exceeds the cap."""

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
                "`python main.py export-csvs --profile full` first. Missing: "
                f"{missing_text}"
            )
        return csv_paths

    def _validate_counts(
        self,
        row_counts: dict[str, int],
        source: str,
    ) -> list[IntegrityCheckResult]:
        results: list[IntegrityCheckResult] = []
        cap = self.settings.max_rows_per_table
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


def _csv_row_count(path: Path) -> int:
    """Count CSV data records while respecting quoted multiline fields."""

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError(f"CSV has no header row: {path}") from exc
        if not header:
            raise ValueError(f"CSV has an empty header row: {path}")
        return sum(1 for _row in reader)
