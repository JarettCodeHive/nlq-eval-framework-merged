"""Deterministic CSV export for Logistics datasets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.base import PROJECT_ROOT
from generators.core.csv_export import CSVExporter
from generators.core.csv_export import CSVExportResult
from generators.core.csv_export import ensure_release_can_be_written
from generators.core.integrity import assert_all_passed
from generators.core.progress import ProgressReporter
from generators.logistics.config import settings_for_profile
from generators.logistics.distributions import LogisticsDistributionApplier
from generators.logistics.generator import LogisticsBaseEntityGenerator
from generators.logistics.imperfections import LogisticsImperfectionInjector
from generators.logistics.validators.config import validate_logistics_config
from generators.logistics.validators.relational import LogisticsRelationalValidator


class LogisticsCSVExporter:
    """Generate, validate, and export deterministic Logistics CSV artifacts."""

    def __init__(
        self,
        generator: DeterministicGenerator,
        progress: ProgressReporter | None = None,
    ) -> None:
        if generator.settings.domain != "logistics":
            raise ValueError("LogisticsCSVExporter only supports logistics")
        validate_logistics_config()
        self.generator = generator
        self.settings = generator.settings
        self.progress = progress

    @classmethod
    def for_profile(
        cls,
        profile: str,
        progress: ProgressReporter | None = None,
    ) -> "LogisticsCSVExporter":
        """Create a Logistics exporter from validated profile configuration."""

        return cls(
            DeterministicGenerator(settings_for_profile(profile)),
            progress=progress,
        )

    def export_full_profile_csvs(self) -> list[CSVExportResult]:
        """Generate and export only validated final Logistics release CSVs."""

        if not self.settings.is_release_profile:
            raise ValueError(
                "Logistics release CSV export requires the full profile; "
                f"got profile={self.settings.profile}"
            )
        self._report("Checking Logistics release output safety")
        ensure_release_can_be_written(self.settings.output_path)
        self._report("Generating validated Logistics stages")
        stages = self.generate_validated_stages()
        self._report("Writing Logistics release CSV files")
        results = self.export_tables(
            stages["imperfect"],
            output_dir=self.settings.output_path,
        )
        self._report("Full-profile Logistics CSV export complete")
        return results

    def export_dev_previews(self) -> dict[str, list[CSVExportResult]]:
        """Generate and write base, distributed, and imperfect dev previews."""

        if self.settings.is_release_profile:
            raise ValueError("Logistics stage previews are available only for dev")
        self._ensure_destination_allowed(self.settings.output_path)
        self._report("Generating Logistics dev preview stages")
        stages = self.generate_validated_stages()
        results: dict[str, list[CSVExportResult]] = {}
        for stage_name, tables in stages.items():
            output_dir = self.settings.output_path / stage_name
            self._report(f"Writing Logistics {stage_name} preview CSV files")
            results[stage_name] = self.export_tables(tables, output_dir=output_dir)
        self._report("Logistics dev preview export complete")
        return results

    def generate_validated_stages(self) -> dict[str, dict[str, Any]]:
        """Build every stage once and run the final relational gate."""

        base = LogisticsBaseEntityGenerator(
            self.generator,
            progress=self.progress,
        ).generate_tables()
        distributed = LogisticsDistributionApplier(
            self.generator,
            progress=self.progress,
        ).apply_to_tables(base)
        imperfect = LogisticsImperfectionInjector(
            self.generator,
            progress=self.progress,
        ).apply_to_tables(distributed)
        self._report("Running final Logistics relational validation")
        results = LogisticsRelationalValidator(self.generator).validate_tables(
            imperfect
        )
        assert_all_passed(results)
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
        """Export Logistics tables in signed configuration order."""

        destination = output_dir or self.settings.output_path
        self._ensure_destination_allowed(destination)
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

    def _ensure_destination_allowed(self, output_dir: Path) -> None:
        """Prevent development artifacts from entering the release tree."""

        if self.settings.is_release_profile:
            return
        release_root = (PROJECT_ROOT / "release").resolve()
        destination = output_dir.resolve()
        if destination == release_root or release_root in destination.parents:
            raise ValueError("Development Logistics output cannot use release paths")

    def _remove_unconfigured_csvs(self, output_dir: Path) -> None:
        """Remove stale CSV files outside the Logistics table contract."""

        if not output_dir.exists():
            return
        configured = {f"{name}.csv" for name in self.settings.table_order}
        for csv_path in output_dir.glob("*.csv"):
            if csv_path.name not in configured:
                csv_path.unlink()

    def _report(self, message: str) -> None:
        if self.progress is not None:
            self.progress.report(message)


__all__ = ["LogisticsCSVExporter"]
