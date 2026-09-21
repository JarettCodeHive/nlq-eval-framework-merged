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
from generators.crm.distributions import CRMDistributionApplier
from generators.crm.generator import CRMBaseEntityGenerator
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
        tables = self.generate_validated_stages()["imperfect"]
        self._report("Writing release CSV files")
        results = self.export_tables(tables)
        self._report("Full-profile CRM CSV export complete")
        return results

    def export_dev_previews(self) -> dict[str, list[CSVExportResult]]:
        """Generate and export validated CRM base, distributed, and final CSVs."""

        if self.settings.is_release_profile:
            raise ValueError("CRM stage previews are available only for dev")
        self._report("Generating CRM dev stages")
        stages = self.generate_validated_stages()
        results: dict[str, list[CSVExportResult]] = {}
        for stage_name, tables in stages.items():
            output_dir = self.settings.output_path / stage_name
            self._report(f"Writing CRM {stage_name} CSV files")
            results[stage_name] = self.export_tables(tables, output_dir=output_dir)
        self._report("CRM dev CSV export complete")
        return results

    def generate_validated_stages(self) -> dict[str, dict[str, Any]]:
        """Generate every CRM stage once and validate the final tables."""

        base = CRMBaseEntityGenerator(
            self.generator,
            progress=self.progress,
        ).generate_tables()
        distributed = CRMDistributionApplier(
            self.generator,
            progress=self.progress,
        ).apply_to_tables(base)
        imperfect = CRMImperfectionInjector(
            self.generator,
            progress=self.progress,
        ).apply_to_tables(distributed)
        self._report("Running final relational validation")
        validation_results = CRMRelationalValidator(self.generator).validate_tables(
            imperfect
        )
        assert_all_passed(validation_results)
        return {
            "base": base,
            "distributed": distributed,
            "imperfect": imperfect,
        }

    def export_tables(
        self,
        tables: dict[str, Any],
        output_dir: Path | None = None,
    ) -> list[CSVExportResult]:
        """Export already-generated CRM tables in signed contract order."""

        destination = output_dir or self.settings.output_path
        self._remove_unconfigured_csvs(destination)
        exporter = CSVExporter(self.settings.csv_format, progress=self.progress)
        return exporter.export_tables(
            tables=tables,
            table_order=self.settings.table_order,
            output_dir=destination,
        )

    @property
    def output_dir(self) -> Path:
        """Return the configured release output directory."""

        return self.settings.output_path

    def _remove_unconfigured_csvs(self, output_dir: Path) -> None:
        """Remove CSVs retired from the configured CRM release contract."""

        if not output_dir.exists():
            return
        configured_names = {
            f"{table_name}.csv" for table_name in self.settings.table_order
        }
        for csv_path in output_dir.glob("*.csv"):
            if csv_path.name not in configured_names:
                csv_path.unlink()

    def _report(self, message: str) -> None:
        """Report progress when the caller supplied a reporter."""

        if self.progress is not None:
            self.progress.report(message)
