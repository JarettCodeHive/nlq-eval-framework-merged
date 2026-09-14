"""CRM full-profile CSV export."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.csv_export import CSVExportResult
from generators.core.csv_export import CSVExporter
from generators.core.csv_export import ensure_release_can_be_written
from generators.core.integrity import assert_all_passed
from generators.core.progress import ProgressReporter
from generators.crm.config import settings_for_profile
from generators.crm.validators.config import validate_crm_config
from generators.crm.imperfections import CRMImperfectionInjector
from generators.crm.validators.relational import CRMRelationalValidator


class CRMCSVExporter:
    """Export CRM imperfect tables as full-profile release CSVs."""

    def __init__(
        self,
        generator: DeterministicGenerator,
        progress: ProgressReporter | None = None,
    ) -> None:
        if generator.settings.domain != "crm":
            raise ValueError("CRMCSVExporter only supports the crm domain")
        validate_crm_config()
        self.generator = generator
        self.settings = generator.settings
        self.progress = progress

    @classmethod
    def for_profile(
        cls,
        profile: str,
        progress: ProgressReporter | None = None,
    ) -> "CRMCSVExporter":
        """Create a CRM CSV exporter from config files."""

        settings = settings_for_profile(profile)
        return cls(DeterministicGenerator(settings), progress=progress)

    def export_full_profile_csvs(self) -> list[CSVExportResult]:
        """Generate, validate, and export CRM full-profile CSVs."""

        if not self.settings.is_release_profile:
            raise ValueError(
                "Step 18 exports release CSVs only for the full profile; "
                f"got profile={self.settings.profile}"
            )

        self._report("Checking release output safety")
        ensure_release_can_be_written(self.settings.output_path)
        self._report("Generating full-profile CRM data")
        tables = CRMImperfectionInjector(
            self.generator, progress=self.progress
        ).generate_imperfect_tables()
        self._report("Running final relational validation")
        validation_results = CRMRelationalValidator(self.generator).validate_tables(
            tables
        )
        assert_all_passed(validation_results)
        self._report("Writing release CSV files")
        results = self.export_tables(tables)
        self._report("Full-profile CRM CSV export complete")
        return results

    def export_tables(self, tables: dict[str, Any]) -> list[CSVExportResult]:
        """Export already-generated CRM tables to the configured output path."""

        self._remove_unconfigured_csvs()
        exporter = CSVExporter(self.settings.csv_format, progress=self.progress)
        return exporter.export_tables(
            tables=tables,
            table_order=self.settings.table_order,
            output_dir=self.settings.output_path,
        )

    @property
    def output_dir(self) -> Path:
        """Return the configured release output directory."""

        return self.settings.output_path

    def _remove_unconfigured_csvs(self) -> None:
        """Remove CSVs retired from the configured CRM release contract."""

        if not self.settings.output_path.exists():
            return
        configured_names = {
            f"{table_name}.csv" for table_name in self.settings.table_order
        }
        for csv_path in self.settings.output_path.glob("*.csv"):
            if csv_path.name not in configured_names:
                csv_path.unlink()

    def _report(self, message: str) -> None:
        """Report progress when the caller supplied a reporter."""

        if self.progress is not None:
            self.progress.report(message)
