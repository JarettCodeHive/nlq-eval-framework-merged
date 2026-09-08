"""CRM full-profile CSV export."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from generators.common.base import DeterministicGenerator
from generators.common.csv_export import CSVExportResult
from generators.common.csv_export import CSVExporter
from generators.common.csv_export import ensure_release_can_be_written
from generators.common.integrity import assert_all_passed
from generators.crm.config import settings_for_profile
from generators.crm.config import validate_crm_config
from generators.crm.imperfections import CRMImperfectionInjector
from generators.crm.integrity import CRMRelationalValidator


class CRMCSVExporter:
    """Export CRM imperfect tables as full-profile release CSVs."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "crm":
            raise ValueError("CRMCSVExporter only supports the crm domain")
        validate_crm_config()
        self.generator = generator
        self.settings = generator.settings

    @classmethod
    def for_profile(cls, profile: str) -> "CRMCSVExporter":
        """Create a CRM CSV exporter from config files."""

        settings = settings_for_profile(profile)
        return cls(DeterministicGenerator(settings))

    def export_full_profile_csvs(self) -> list[CSVExportResult]:
        """Generate, validate, and export CRM full-profile CSVs."""

        if not self.settings.is_release_profile:
            raise ValueError(
                "Step 18 exports release CSVs only for the full profile; "
                f"got profile={self.settings.profile}"
            )

        ensure_release_can_be_written(self.settings.output_path)
        tables = CRMImperfectionInjector(self.generator).generate_imperfect_tables()
        validation_results = CRMRelationalValidator(self.generator).validate_tables(
            tables
        )
        assert_all_passed(validation_results)
        return self.export_tables(tables)

    def export_tables(self, tables: dict[str, Any]) -> list[CSVExportResult]:
        """Export already-generated CRM tables to the configured output path."""

        self._remove_unconfigured_csvs()
        exporter = CSVExporter(self.settings.csv_format)
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
