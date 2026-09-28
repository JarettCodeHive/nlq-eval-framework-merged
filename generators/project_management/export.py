"""Deterministic CSV export for Project Management datasets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.csv_export import CSVExporter
from generators.core.csv_export import CSVExportResult
from generators.core.csv_export import ensure_release_can_be_written
from generators.core.integrity import assert_all_passed
from generators.core.progress import ProgressReporter
from generators.project_management.config import settings_for_profile
from generators.project_management.distributions import (
    ProjectManagementDistributionApplier,
)
from generators.project_management.generator import (
    ProjectManagementBaseEntityGenerator,
)
from generators.project_management.imperfections import (
    ProjectManagementImperfectionInjector,
)
from generators.project_management.validators.config import (
    validate_project_management_config,
)
from generators.project_management.validators.relational import (
    ProjectManagementRelationalValidator,
)


class ProjectManagementCSVExporter:
    """Generate, validate, and export deterministic PM CSV artifacts."""

    def __init__(
        self,
        generator: DeterministicGenerator,
        progress: ProgressReporter | None = None,
    ) -> None:
        if generator.settings.domain != "project_management":
            raise ValueError(
                "ProjectManagementCSVExporter only supports project_management"
            )
        validate_project_management_config()
        self.generator = generator
        self.settings = generator.settings
        self.progress = progress

    @classmethod
    def for_profile(
        cls,
        profile: str,
        progress: ProgressReporter | None = None,
    ) -> "ProjectManagementCSVExporter":
        """Create a PM CSV exporter from validated configuration."""

        return cls(
            DeterministicGenerator(settings_for_profile(profile)),
            progress=progress,
        )

    def export_full_profile_csvs(self) -> list[CSVExportResult]:
        """Generate and export only validated final PM release CSVs."""

        if not self.settings.is_release_profile:
            raise ValueError(
                "Project Management release CSV export requires the full profile; "
                f"got profile={self.settings.profile}"
            )
        self._report("Checking Project Management release output safety")
        ensure_release_can_be_written(self.settings.output_path)
        self._report("Generating Project Management validated stages")
        stages = self.generate_validated_stages()
        self._report("Writing Project Management release CSV files")
        results = self.export_tables(
            stages["imperfect"],
            output_dir=self.settings.output_path,
        )
        self._report("Full-profile Project Management CSV export complete")
        return results

    def export_dev_previews(self) -> dict[str, list[CSVExportResult]]:
        """Generate and write base, distributed, and imperfect dev previews."""

        if self.settings.is_release_profile:
            raise ValueError(
                "Project Management stage previews are available only for dev"
            )
        self._report("Generating Project Management dev preview stages")
        stages = self.generate_validated_stages()
        results: dict[str, list[CSVExportResult]] = {}
        for stage_name, tables in stages.items():
            output_dir = self.settings.output_path / stage_name
            self._report(f"Writing Project Management {stage_name} preview CSVs")
            results[stage_name] = self.export_tables(tables, output_dir=output_dir)
        self._report("Project Management dev preview export complete")
        return results

    def generate_validated_stages(self) -> dict[str, dict[str, Any]]:
        """Build each stage once and run the final relational/date gate."""

        base = ProjectManagementBaseEntityGenerator(
            self.generator,
            progress=self.progress,
        ).generate_tables()
        distributed = ProjectManagementDistributionApplier(
            self.generator,
            progress=self.progress,
        ).apply_to_tables(base)
        imperfect = ProjectManagementImperfectionInjector(
            self.generator,
            progress=self.progress,
        ).apply_to_tables(distributed)
        self._report("Running final Project Management relational validation")
        validation_results = ProjectManagementRelationalValidator(
            self.generator
        ).validate_tables(imperfect)
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
        """Export PM tables in signed contract order."""

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
        """Remove stale CSV files outside the PM table contract."""

        if not output_dir.exists():
            return
        configured = {f"{name}.csv" for name in self.settings.table_order}
        for csv_path in output_dir.glob("*.csv"):
            if csv_path.name not in configured:
                csv_path.unlink()

    def _report(self, message: str) -> None:
        if self.progress is not None:
            self.progress.report(message)


__all__ = ["ProjectManagementCSVExporter"]
