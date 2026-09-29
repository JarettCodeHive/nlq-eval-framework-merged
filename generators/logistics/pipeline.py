"""End-to-end Logistics dataset workflows for dev and full profiles."""

from __future__ import annotations

from pathlib import Path

from generators.core.base import DeterministicGenerator
from generators.core.integrity import IntegrityCheckResult
from generators.core.integrity import assert_all_passed
from generators.core.progress import ProgressReporter
from generators.logistics.config import settings_for_profile
from generators.logistics.data_dictionary import LogisticsDataDictionaryGenerator
from generators.logistics.export import LogisticsCSVExporter
from generators.logistics.hashes import LogisticsHashComputer
from generators.logistics.manifest import LogisticsManifestGenerator
from generators.logistics.schema_sql import LogisticsSchemaSQLGenerator
from generators.logistics.validators.config import validate_logistics_config
from generators.logistics.validators.fk_integrity import (
    LogisticsDuckDBFKValidator,
)
from generators.logistics.validators.imperfection_rates import (
    LogisticsImperfectionRateValidator,
)
from generators.logistics.validators.join_paths import LogisticsJoinPathValidator
from generators.logistics.validators.reproducibility import (
    LogisticsReproducibilityValidator,
)
from generators.logistics.validators.row_caps import LogisticsRowCapValidator


class LogisticsDatasetPipeline:
    """Build and validate persisted Logistics artifacts for one profile."""

    def __init__(
        self,
        generator: DeterministicGenerator,
        progress: ProgressReporter | None = None,
    ) -> None:
        if generator.settings.domain != "logistics":
            raise ValueError("LogisticsDatasetPipeline only supports logistics")
        self.generator = generator
        self.settings = generator.settings
        self.progress = progress or ProgressReporter()
        self.exporter = LogisticsCSVExporter(generator, progress=self.progress)
        self.row_caps = LogisticsRowCapValidator(generator)
        self.fk_validator = LogisticsDuckDBFKValidator(generator)
        self.join_validator = LogisticsJoinPathValidator(generator)
        self.imperfection_validator = LogisticsImperfectionRateValidator(generator)
        self.hash_computer = LogisticsHashComputer(generator)
        self.dictionary_generator = LogisticsDataDictionaryGenerator(generator)
        self.schema_generator = LogisticsSchemaSQLGenerator(generator)
        self.reproducibility_validator = LogisticsReproducibilityValidator(
            DeterministicGenerator(self.settings)
        )
        self.manifest_generator = LogisticsManifestGenerator(generator)

    @classmethod
    def for_profile(
        cls,
        profile: str,
        progress: ProgressReporter | None = None,
    ) -> "LogisticsDatasetPipeline":
        """Create a Logistics pipeline from validated profile settings."""

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
        self._step(
            1,
            total,
            "Validate Logistics configuration and expected row caps",
        )
        validate_logistics_config()
        self._assert_gate(self.row_caps.validate_expected_counts())

        self._step(
            2,
            total,
            "Generate, validate, and export Logistics stage CSVs",
        )
        self.exporter.export_dev_previews()
        final_dir = self.settings.output_path / "imperfect"

        self._step(3, total, "Validate persisted Logistics row caps")
        self._assert_gate(self.row_caps.validate_csv_directory(final_dir))

        self._step(
            4,
            total,
            "Validate persisted Logistics DDL, foreign keys, and orphan policy",
        )
        self._assert_gate(self.fk_validator.validate_csv_directory(final_dir))

        self._step(5, total, "Validate persisted Logistics join paths")
        self._assert_gate(self.join_validator.validate_csv_directory(final_dir))

        self._step(6, total, "Validate persisted Logistics imperfection rates")
        self._assert_gate(self.imperfection_validator.validate_csv_directory(final_dir))
        self.progress.report(f"Logistics dev dataset build complete: {final_dir}")
        return final_dir

    def _run_full(self) -> Path:
        total = 11
        self._step(
            1,
            total,
            "Validate Logistics configuration and expected row caps",
        )
        validate_logistics_config()
        self._assert_gate(self.row_caps.validate_expected_counts())

        self._step(2, total, "Generate and export Logistics release CSVs")
        self.exporter.export_full_profile_csvs()
        release_dir = self.settings.output_path

        self._step(3, total, "Validate persisted Logistics release row caps")
        self._assert_gate(self.row_caps.validate_csv_directory(release_dir))

        self._step(
            4,
            total,
            "Validate persisted Logistics DDL, foreign keys, and orphan policy",
        )
        self._assert_gate(self.fk_validator.validate_csv_directory(release_dir))

        self._step(5, total, "Validate persisted Logistics join paths")
        self._assert_gate(self.join_validator.validate_csv_directory(release_dir))

        self._step(6, total, "Validate persisted Logistics imperfection rates")
        self._assert_gate(
            self.imperfection_validator.validate_csv_directory(release_dir)
        )

        self._step(7, total, "Compute Logistics release CSV SHA-256 hashes")
        self.hash_computer.compute_exported_csv_hashes()

        self._step(8, total, "Generate Logistics release data dictionary")
        self.dictionary_generator.write_release_dictionary()

        self._step(9, total, "Generate Logistics release schema SQL")
        self.schema_generator.write_release_schema()

        self._step(10, total, "Validate clean-room Logistics reproducibility")
        self._assert_gate(self.reproducibility_validator.validate_release())

        self._step(11, total, "Generate final immutable Logistics manifest")
        manifest_path = self.manifest_generator.write_manifest()
        self.progress.report(f"Logistics full release build complete: {release_dir}")
        return manifest_path

    def _step(self, number: int, total: int, message: str) -> None:
        self.progress.report(f"Step {number}/{total}: {message}")

    @staticmethod
    def _assert_gate(results: list[IntegrityCheckResult]) -> None:
        assert_all_passed(results)


__all__ = ["LogisticsDatasetPipeline"]
