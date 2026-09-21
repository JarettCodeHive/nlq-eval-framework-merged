"""Deterministic CSV export for Sales datasets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.csv_export import CSVExportResult
from generators.core.csv_export import CSVExporter
from generators.core.csv_export import ensure_release_can_be_written
from generators.core.integrity import assert_all_passed
from generators.core.progress import ProgressReporter
from generators.sales.config import settings_for_profile
from generators.sales.distributions import SalesDistributionApplier
from generators.sales.generator import SalesBaseEntityGenerator
from generators.sales.imperfections import SalesImperfectionInjector
from generators.sales.validators.config import validate_sales_config
from generators.sales.validators.relational import SalesRelationalValidator


class SalesCSVExporter:
    """Generate, validate, and export deterministic Sales CSV artifacts."""

    def __init__(
        self,
        generator: DeterministicGenerator,
        progress: ProgressReporter | None = None,
    ) -> None:
        if generator.settings.domain != "sales":
            raise ValueError("SalesCSVExporter only supports sales")
        validate_sales_config()
        self.generator = generator
        self.settings = generator.settings
        self.progress = progress

    @classmethod
    def for_profile(
        cls,
        profile: str,
        progress: ProgressReporter | None = None,
    ) -> "SalesCSVExporter":
        """Create a Sales CSV exporter from validated config files."""

        return cls(
            DeterministicGenerator(settings_for_profile(profile)),
            progress=progress,
        )

    def export_full_profile_csvs(self) -> list[CSVExportResult]:
        """Generate and export the validated full Sales release CSVs."""

        if not self.settings.is_release_profile:
            raise ValueError(
                "Sales release CSV export requires the full profile; "
                f"got profile={self.settings.profile}"
            )
        self._report("Checking Sales release output safety")
        ensure_release_can_be_written(self.settings.output_path)
        self._report("Generating Sales base, distributed, and imperfect stages")
        stages = self.generate_validated_stages()
        self._report("Writing Sales release CSV files")
        results = self.export_tables(
            stages["imperfect"],
            output_dir=self.settings.output_path,
        )
        self._report("Full-profile Sales CSV export complete")
        return results

    def export_dev_previews(self) -> dict[str, list[CSVExportResult]]:
        """Explicitly generate and write base, distributed, and imperfect previews."""

        if self.settings.is_release_profile:
            raise ValueError("Sales stage previews are available only for dev")
        self._report("Generating Sales dev preview stages")
        stages = self.generate_validated_stages()
        results: dict[str, list[CSVExportResult]] = {}
        for stage_name, tables in stages.items():
            output_dir = self.settings.output_path / stage_name
            self._report(f"Writing Sales {stage_name} preview CSV files")
            results[stage_name] = self.export_tables(tables, output_dir=output_dir)
        self._report("Sales dev preview export complete")
        return results

    def generate_validated_stages(self) -> dict[str, dict[str, Any]]:
        """Build all stages in one in-memory chain and validate the final stage."""

        base = SalesBaseEntityGenerator(
            self.generator,
            progress=self.progress,
        ).generate_tables()
        distributed = SalesDistributionApplier(
            self.generator,
            progress=self.progress,
        ).apply_to_tables(base)
        imperfect = SalesImperfectionInjector(
            self.generator,
            progress=self.progress,
        ).apply_to_tables(distributed)
        self._report("Running final Sales relational validation")
        validation_results = SalesRelationalValidator(self.generator).validate_tables(
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
        """Export an already-generated Sales table mapping in contract order."""

        destination = output_dir or self.settings.output_path
        self._remove_unconfigured_csvs(destination)
        return CSVExporter(
            self.settings.csv_format,
            progress=self.progress,
        ).export_tables(
            tables=tables,
            table_order=self.settings.table_order,
            output_dir=destination,
        )

    @property
    def output_dir(self) -> Path:
        """Return the configured profile output directory."""

        return self.settings.output_path

    def _remove_unconfigured_csvs(self, output_dir: Path) -> None:
        """Remove stale CSVs that are outside the Sales table contract."""

        if not output_dir.exists():
            return
        configured = {f"{name}.csv" for name in self.settings.table_order}
        for csv_path in output_dir.glob("*.csv"):
            if csv_path.name not in configured:
                csv_path.unlink()

    def _report(self, message: str) -> None:
        if self.progress is not None:
            self.progress.report(message)


__all__ = ["SalesCSVExporter"]
