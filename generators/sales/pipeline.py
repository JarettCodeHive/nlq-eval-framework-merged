"""End-to-end Sales dataset build workflows for dev and full profiles."""

from __future__ import annotations

from pathlib import Path

from generators.core.base import DeterministicGenerator
from generators.core.integrity import IntegrityCheckResult
from generators.core.integrity import assert_all_passed
from generators.core.progress import ProgressReporter
from generators.sales.config import settings_for_profile
from generators.sales.data_dictionary import SalesDataDictionaryGenerator
from generators.sales.export import SalesCSVExporter
from generators.sales.hashes import SalesHashComputer
from generators.sales.manifest import SalesManifestGenerator
from generators.sales.schema_sql import SalesSchemaSQLGenerator
from generators.sales.validators.config import validate_sales_config
from generators.sales.validators.fk_integrity import SalesDuckDBFKValidator
from generators.sales.validators.imperfection_rates import (
    SalesImperfectionRateValidator,
)
from generators.sales.validators.join_paths import SalesJoinPathValidator
from generators.sales.validators.reproducibility import (
    SalesReproducibilityValidator,
)
from generators.sales.validators.row_caps import SalesRowCapValidator


class SalesDatasetPipeline:
    """Build and validate persisted Sales CSV artifacts for one profile.

    Dev writes all generation stages and validates the persisted ``imperfect``
    CSVs. Full writes the versioned release, runs every release gate against
    disk, generates supporting artifacts, verifies clean-room reproducibility,
    and writes ``manifest.json`` last to seal the release.
    """

    def __init__(
        self,
        generator: DeterministicGenerator,
        progress: ProgressReporter | None = None,
    ) -> None:
        if generator.settings.domain != "sales":
            raise ValueError("SalesDatasetPipeline only supports the sales domain")
        self.generator = generator
        self.settings = generator.settings
        self.progress = progress or ProgressReporter()
        self.exporter = SalesCSVExporter(generator, progress=self.progress)
        self.row_caps = SalesRowCapValidator(generator)
        self.fk_validator = SalesDuckDBFKValidator(generator)
        self.join_validator = SalesJoinPathValidator(generator)
        self.imperfection_validator = SalesImperfectionRateValidator(generator)
        self.hash_computer = SalesHashComputer(generator)
        self.dictionary_generator = SalesDataDictionaryGenerator(generator)
        self.schema_generator = SalesSchemaSQLGenerator(generator)
        self.reproducibility_validator = SalesReproducibilityValidator(
            DeterministicGenerator(self.settings)
        )
        self.manifest_generator = SalesManifestGenerator(generator)

    @classmethod
    def for_profile(
        cls,
        profile: str,
        progress: ProgressReporter | None = None,
    ) -> "SalesDatasetPipeline":
        """Create a Sales pipeline from validated profile settings."""

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
        self._step(1, total, "Validate Sales configuration and expected row caps")
        validate_sales_config()
        self._assert_gate(self.row_caps.validate_expected_counts())

        self._step(2, total, "Generate, validate, and export Sales stage CSVs")
        self.exporter.export_dev_previews()
        final_dir = self.settings.output_path / "imperfect"

        self._step(3, total, "Validate persisted Sales row caps")
        self._assert_gate(self.row_caps.validate_csv_directory(final_dir))

        self._step(4, total, "Validate persisted Sales DDL and foreign keys")
        self._assert_gate(self.fk_validator.validate_csv_directory(final_dir))

        self._step(5, total, "Validate persisted Sales join paths")
        self._assert_gate(self.join_validator.validate_csv_directory(final_dir))

        self._step(6, total, "Validate persisted Sales imperfection rates")
        self._assert_gate(self.imperfection_validator.validate_csv_directory(final_dir))
        self.progress.report(f"Sales dev dataset build complete: {final_dir}")
        return final_dir

    def _run_full(self) -> Path:
        total = 11
        self._step(1, total, "Validate Sales configuration and expected row caps")
        validate_sales_config()
        self._assert_gate(self.row_caps.validate_expected_counts())

        self._step(2, total, "Generate, validate, and export Sales release CSVs")
        self.exporter.export_full_profile_csvs()
        release_dir = self.settings.output_path

        self._step(3, total, "Validate persisted Sales release row caps")
        self._assert_gate(self.row_caps.validate_csv_directory(release_dir))

        self._step(4, total, "Validate persisted Sales DDL and foreign keys")
        self._assert_gate(self.fk_validator.validate_csv_directory(release_dir))

        self._step(5, total, "Validate persisted Sales join paths")
        self._assert_gate(self.join_validator.validate_csv_directory(release_dir))

        self._step(6, total, "Validate persisted Sales imperfection rates")
        self._assert_gate(
            self.imperfection_validator.validate_csv_directory(release_dir)
        )

        self._step(7, total, "Compute Sales release CSV SHA-256 hashes")
        self.hash_computer.compute_exported_csv_hashes()

        self._step(8, total, "Generate Sales release data dictionary")
        self.dictionary_generator.write_release_dictionary()

        self._step(9, total, "Generate Sales release schema SQL")
        self.schema_generator.write_release_schema()

        self._step(10, total, "Validate clean-room Sales reproducibility")
        self._assert_gate(self.reproducibility_validator.validate_release())

        self._step(11, total, "Generate final immutable Sales manifest")
        manifest_path = self.manifest_generator.write_manifest()
        self.progress.report(f"Sales full release build complete: {release_dir}")
        return manifest_path

    def _step(self, number: int, total: int, message: str) -> None:
        """Report one numbered high-level pipeline step."""

        self.progress.report(f"Step {number}/{total}: {message}")

    @staticmethod
    def _assert_gate(results: list[IntegrityCheckResult]) -> None:
        """Raise one combined error when a pipeline validation gate fails."""

        assert_all_passed(results)


__all__ = ["SalesDatasetPipeline"]
