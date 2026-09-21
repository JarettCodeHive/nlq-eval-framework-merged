"""Deterministic CSV export for Finance datasets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.csv_export import CSVExportResult
from generators.core.csv_export import CSVExporter
from generators.core.csv_export import ensure_release_can_be_written
from generators.core.integrity import assert_all_passed
from generators.core.progress import ProgressReporter
from generators.finance.config import settings_for_profile
from generators.finance.distributions import FinanceDistributionApplier
from generators.finance.generator import FinanceBaseEntityGenerator
from generators.finance.imperfections import FinanceImperfectionInjector
from generators.finance.validators.config import validate_finance_config
from generators.finance.validators.relational import FinanceRelationalValidator


class FinanceCSVExporter:
    """Generate, validate, and export deterministic Finance CSV artifacts."""

    def __init__(
        self,
        generator: DeterministicGenerator,
        progress: ProgressReporter | None = None,
    ) -> None:
        if generator.settings.domain != "finance":
            raise ValueError("FinanceCSVExporter only supports finance")
        validate_finance_config()
        self.generator = generator
        self.settings = generator.settings
        self.progress = progress

    @classmethod
    def for_profile(
        cls,
        profile: str,
        progress: ProgressReporter | None = None,
    ) -> "FinanceCSVExporter":
        """Create a Finance CSV exporter from validated config files."""

        return cls(
            DeterministicGenerator(settings_for_profile(profile)),
            progress=progress,
        )

    def export_full_profile_csvs(self) -> list[CSVExportResult]:
        """Generate and export only the validated final Finance release CSVs."""

        if not self.settings.is_release_profile:
            raise ValueError(
                "Finance release CSV export requires the full profile; "
                f"got profile={self.settings.profile}"
            )
        self._report("Checking Finance release output safety")
        ensure_release_can_be_written(self.settings.output_path)
        self._report("Generating Finance base, distributed, and imperfect stages")
        stages = self.generate_validated_stages()
        self._report("Writing Finance release CSV files")
        results = self.export_tables(
            stages["imperfect"],
            output_dir=self.settings.output_path,
        )
        self._report("Full-profile Finance CSV export complete")
        return results

    def export_dev_previews(self) -> dict[str, list[CSVExportResult]]:
        """Generate and write validated base, distributed, and imperfect previews."""

        if self.settings.is_release_profile:
            raise ValueError("Finance stage previews are available only for dev")
        self._report("Generating Finance dev preview stages")
        stages = self.generate_validated_stages()
        results: dict[str, list[CSVExportResult]] = {}
        for stage_name, tables in stages.items():
            output_dir = self.settings.output_path / stage_name
            self._report(f"Writing Finance {stage_name} preview CSV files")
            results[stage_name] = self.export_tables(tables, output_dir=output_dir)
        self._report("Finance dev preview export complete")
        return results

    def generate_validated_stages(self) -> dict[str, dict[str, Any]]:
        """Build every stage once in memory and validate the final dataset."""

        base = FinanceBaseEntityGenerator(
            self.generator,
            progress=self.progress,
        ).generate_tables()
        distributed = FinanceDistributionApplier(
            self.generator,
            progress=self.progress,
        ).apply_to_tables(base)
        imperfect = FinanceImperfectionInjector(
            self.generator,
            progress=self.progress,
        ).apply_to_tables(distributed)
        self._report("Running final Finance relational and accounting validation")
        validation_results = FinanceRelationalValidator(
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
        """Export a Finance table mapping in signed contract order."""

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
        """Remove stale CSV files outside the Finance table contract."""

        if not output_dir.exists():
            return
        configured = {f"{name}.csv" for name in self.settings.table_order}
        for csv_path in output_dir.glob("*.csv"):
            if csv_path.name not in configured:
                csv_path.unlink()

    def _report(self, message: str) -> None:
        if self.progress is not None:
            self.progress.report(message)


__all__ = ["FinanceCSVExporter"]
