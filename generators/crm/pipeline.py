"""End-to-end CRM dataset build workflows for dev and full profiles."""

from __future__ import annotations

from pathlib import Path

from generators.core.base import DeterministicGenerator
from generators.core.integrity import IntegrityCheckResult
from generators.core.integrity import assert_all_passed
from generators.core.progress import ProgressReporter
from generators.crm.config import settings_for_profile
from generators.crm.data_dictionary import CRMDataDictionaryGenerator
from generators.crm.export import CRMCSVExporter
from generators.crm.hashes import CRMHashComputer
from generators.crm.manifest import CRMManifestGenerator
from generators.crm.schema_sql import CRMSchemaSQLGenerator
from generators.crm.validators.config import validate_crm_config
from generators.crm.validators.fk_integrity import CRMDuckDBFKValidator
from generators.crm.validators.imperfection_rates import CRMImperfectionRateValidator
from generators.crm.validators.join_paths import CRMJoinPathValidator
from generators.crm.validators.reproducibility import CRMReproducibilityValidator
from generators.crm.validators.row_caps import CRMRowCapValidator


class CRMDatasetPipeline:
    """Build and validate persisted CRM CSV artifacts for one profile.

    Generation still uses dataframes internally, but every post-export gate in
    this workflow reads the written CSV files. Dev writes all three stages and
    validates ``imperfect/``. Full writes the versioned release, generates its
    support artifacts, verifies clean-room reproducibility, and seals it by
    writing ``manifest.json`` last.
    """

    def __init__(
        self,
        generator: DeterministicGenerator,
        progress: ProgressReporter | None = None,
    ) -> None:
        if generator.settings.domain != "crm":
            raise ValueError("CRMDatasetPipeline only supports the crm domain")
        self.generator = generator
        self.settings = generator.settings
        self.progress = progress or ProgressReporter()
        self.exporter = CRMCSVExporter(generator, progress=self.progress)
        self.row_caps = CRMRowCapValidator(generator)
        self.fk_validator = CRMDuckDBFKValidator(generator)
        self.join_validator = CRMJoinPathValidator(generator)
        self.imperfection_validator = CRMImperfectionRateValidator(generator)
        self.hash_computer = CRMHashComputer(generator)
        self.dictionary_generator = CRMDataDictionaryGenerator(generator)
        self.schema_generator = CRMSchemaSQLGenerator(generator)
        # Clean-room regeneration must not inherit RNG stream state consumed by
        # the release build performed earlier in this pipeline.
        self.reproducibility_validator = CRMReproducibilityValidator(
            DeterministicGenerator(self.settings)
        )
        self.manifest_generator = CRMManifestGenerator(generator)

    @classmethod
    def for_profile(
        cls,
        profile: str,
        progress: ProgressReporter | None = None,
    ) -> "CRMDatasetPipeline":
        """Create a CRM pipeline from validated profile settings."""

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
        self._step(1, total, "Validate CRM configuration and expected row caps")
        validate_crm_config()
        self._assert_gate(self.row_caps.validate_expected_counts())

        self._step(2, total, "Generate, validate, and export CRM stage CSVs")
        self.exporter.export_dev_previews()
        final_dir = self.settings.output_path / "imperfect"

        self._step(3, total, "Validate persisted CRM row caps")
        self._assert_gate(self.row_caps.validate_csv_directory(final_dir))

        self._step(4, total, "Validate persisted CRM DDL and foreign keys")
        self._assert_gate(self.fk_validator.validate_csv_directory(final_dir))

        self._step(5, total, "Validate persisted CRM join paths")
        self._assert_gate(self.join_validator.validate_csv_directory(final_dir))

        self._step(6, total, "Validate persisted CRM imperfection rates")
        self._assert_gate(self.imperfection_validator.validate_csv_directory(final_dir))
        self.progress.report(f"CRM dev dataset build complete: {final_dir}")
        return final_dir

    def _run_full(self) -> Path:
        total = 11
        self._step(1, total, "Validate CRM configuration and expected row caps")
        validate_crm_config()
        self._assert_gate(self.row_caps.validate_expected_counts())

        self._step(2, total, "Generate, validate, and export CRM release CSVs")
        self.exporter.export_full_profile_csvs()
        release_dir = self.settings.output_path

        self._step(3, total, "Validate persisted CRM release row caps")
        self._assert_gate(self.row_caps.validate_csv_directory(release_dir))

        self._step(4, total, "Validate persisted CRM DDL and foreign keys")
        self._assert_gate(self.fk_validator.validate_csv_directory(release_dir))

        self._step(5, total, "Validate persisted CRM join paths")
        self._assert_gate(self.join_validator.validate_csv_directory(release_dir))

        self._step(6, total, "Validate persisted CRM imperfection rates")
        self._assert_gate(
            self.imperfection_validator.validate_csv_directory(release_dir)
        )

        self._step(7, total, "Compute CRM release CSV SHA-256 hashes")
        self.hash_computer.compute_exported_csv_hashes()

        self._step(8, total, "Generate CRM release data dictionary")
        self.dictionary_generator.write_release_dictionary()

        self._step(9, total, "Generate CRM release schema SQL")
        self.schema_generator.write_release_schema()

        self._step(10, total, "Validate clean-room CRM reproducibility")
        self._assert_gate(self.reproducibility_validator.validate_release())

        self._step(11, total, "Generate final immutable CRM manifest")
        manifest_path = self.manifest_generator.write_manifest()
        self.progress.report(f"CRM full release build complete: {release_dir}")
        return manifest_path

    def _step(self, number: int, total: int, message: str) -> None:
        """Report one numbered high-level pipeline step."""

        self.progress.report(f"Step {number}/{total}: {message}")

    @staticmethod
    def _assert_gate(results: list[IntegrityCheckResult]) -> None:
        """Raise one combined error when a pipeline validation gate fails."""

        assert_all_passed(results)


__all__ = ["CRMDatasetPipeline"]
