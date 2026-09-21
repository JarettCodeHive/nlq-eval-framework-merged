"""End-to-end Finance dataset build workflows for dev and full profiles."""

from __future__ import annotations

from pathlib import Path

from generators.core.base import DeterministicGenerator
from generators.core.integrity import IntegrityCheckResult
from generators.core.integrity import assert_all_passed
from generators.core.progress import ProgressReporter
from generators.finance.config import settings_for_profile
from generators.finance.data_dictionary import FinanceDataDictionaryGenerator
from generators.finance.export import FinanceCSVExporter
from generators.finance.hashes import FinanceHashComputer
from generators.finance.manifest import FinanceManifestGenerator
from generators.finance.schema_sql import FinanceSchemaSQLGenerator
from generators.finance.validators.config import validate_finance_config
from generators.finance.validators.fk_integrity import FinanceDuckDBFKValidator
from generators.finance.validators.imperfection_rates import (
    FinanceImperfectionRateValidator,
)
from generators.finance.validators.join_paths import FinanceJoinPathValidator
from generators.finance.validators.reproducibility import (
    FinanceReproducibilityValidator,
)
from generators.finance.validators.row_caps import FinanceRowCapValidator


class FinanceDatasetPipeline:
    """Build and validate persisted Finance CSV artifacts for one profile.

    Dev writes all three generation stages and validates the persisted
    ``imperfect`` CSVs. Full runs row-cap, DDL/FK, accounting, FX, join-path,
    imperfection, artifact, and reproducibility gates before writing the final
    immutable manifest.
    """

    def __init__(
        self,
        generator: DeterministicGenerator,
        progress: ProgressReporter | None = None,
    ) -> None:
        if generator.settings.domain != "finance":
            raise ValueError("FinanceDatasetPipeline only supports finance")
        self.generator = generator
        self.settings = generator.settings
        self.progress = progress or ProgressReporter()
        self.exporter = FinanceCSVExporter(generator, progress=self.progress)
        self.row_caps = FinanceRowCapValidator(generator)
        self.fk_validator = FinanceDuckDBFKValidator(generator)
        self.join_validator = FinanceJoinPathValidator(generator)
        self.imperfection_validator = FinanceImperfectionRateValidator(generator)
        self.hash_computer = FinanceHashComputer(generator)
        self.dictionary_generator = FinanceDataDictionaryGenerator(generator)
        self.schema_generator = FinanceSchemaSQLGenerator(generator)
        self.reproducibility_validator = FinanceReproducibilityValidator(
            DeterministicGenerator(self.settings)
        )
        self.manifest_generator = FinanceManifestGenerator(generator)

    @classmethod
    def for_profile(
        cls,
        profile: str,
        progress: ProgressReporter | None = None,
    ) -> "FinanceDatasetPipeline":
        """Create a Finance pipeline from validated profile settings."""

        return cls(
            DeterministicGenerator(settings_for_profile(profile)),
            progress=progress,
        )

    def run(self) -> Path:
        """Run the complete persisted-data workflow for the selected profile."""

        if self.settings.is_release_profile:
            return self._run_full()
        return self._run_dev()

    def _run_dev(self) -> Path:
        total = 6
        self._step(1, total, "Validate Finance configuration and expected row caps")
        validate_finance_config()
        self._assert_gate(self.row_caps.validate_expected_counts())

        self._step(2, total, "Generate, validate, and export Finance stage CSVs")
        self.exporter.export_dev_previews()
        final_dir = self.settings.output_path / "imperfect"

        self._step(3, total, "Validate persisted Finance row caps")
        self._assert_gate(self.row_caps.validate_csv_directory(final_dir))

        self._step(
            4,
            total,
            "Validate persisted Finance DDL, foreign keys, and accounting rules",
        )
        self._assert_gate(self.fk_validator.validate_csv_directory(final_dir))

        self._step(5, total, "Validate persisted Finance join and FX paths")
        self._assert_gate(self.join_validator.validate_csv_directory(final_dir))

        self._step(6, total, "Validate persisted Finance imperfection rates")
        self._assert_gate(self.imperfection_validator.validate_csv_directory(final_dir))
        self.progress.report(f"Finance dev dataset build complete: {final_dir}")
        return final_dir

    def _run_full(self) -> Path:
        total = 11
        self._step(1, total, "Validate Finance configuration and expected row caps")
        validate_finance_config()
        self._assert_gate(self.row_caps.validate_expected_counts())

        self._step(2, total, "Generate, validate, and export Finance release CSVs")
        self.exporter.export_full_profile_csvs()
        release_dir = self.settings.output_path

        self._step(3, total, "Validate persisted Finance release row caps")
        self._assert_gate(self.row_caps.validate_csv_directory(release_dir))

        self._step(
            4,
            total,
            "Validate persisted Finance DDL, foreign keys, and accounting rules",
        )
        self._assert_gate(self.fk_validator.validate_csv_directory(release_dir))

        self._step(5, total, "Validate persisted Finance join and FX paths")
        self._assert_gate(self.join_validator.validate_csv_directory(release_dir))

        self._step(6, total, "Validate persisted Finance imperfection rates")
        self._assert_gate(
            self.imperfection_validator.validate_csv_directory(release_dir)
        )

        self._step(7, total, "Compute Finance release CSV SHA-256 hashes")
        self.hash_computer.compute_exported_csv_hashes()

        self._step(8, total, "Generate Finance release data dictionary")
        self.dictionary_generator.write_release_dictionary()

        self._step(9, total, "Generate Finance release schema SQL")
        self.schema_generator.write_release_schema()

        self._step(10, total, "Validate clean-room Finance reproducibility")
        self._assert_gate(self.reproducibility_validator.validate_release())

        self._step(11, total, "Generate final immutable Finance manifest")
        manifest_path = self.manifest_generator.write_manifest()
        self.progress.report(f"Finance full release build complete: {release_dir}")
        return manifest_path

    def _step(self, number: int, total: int, message: str) -> None:
        """Report one numbered high-level pipeline step."""

        self.progress.report(f"Step {number}/{total}: {message}")

    @staticmethod
    def _assert_gate(results: list[IntegrityCheckResult]) -> None:
        """Raise one combined error when a pipeline validation gate fails."""

        assert_all_passed(results)


__all__ = ["FinanceDatasetPipeline"]
