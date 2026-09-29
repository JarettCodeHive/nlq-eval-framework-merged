"""End-to-end Project Management dataset workflows for dev and full profiles."""

from __future__ import annotations

from pathlib import Path

from generators.core.base import DeterministicGenerator
from generators.core.integrity import IntegrityCheckResult
from generators.core.integrity import assert_all_passed
from generators.core.progress import ProgressReporter
from generators.project_management.config import settings_for_profile
from generators.project_management.data_dictionary import (
    ProjectManagementDataDictionaryGenerator,
)
from generators.project_management.export import ProjectManagementCSVExporter
from generators.project_management.hashes import ProjectManagementHashComputer
from generators.project_management.manifest import (
    ProjectManagementManifestGenerator,
)
from generators.project_management.schema_sql import (
    ProjectManagementSchemaSQLGenerator,
)
from generators.project_management.validators.config import (
    validate_project_management_config,
)
from generators.project_management.validators.fk_integrity import (
    ProjectManagementDuckDBFKValidator,
)
from generators.project_management.validators.imperfection_rates import (
    ProjectManagementImperfectionRateValidator,
)
from generators.project_management.validators.join_paths import (
    ProjectManagementJoinPathValidator,
)
from generators.project_management.validators.reproducibility import (
    ProjectManagementReproducibilityValidator,
)
from generators.project_management.validators.row_caps import (
    ProjectManagementRowCapValidator,
)


class ProjectManagementDatasetPipeline:
    """Build and validate persisted PM artifacts for one profile."""

    def __init__(
        self,
        generator: DeterministicGenerator,
        progress: ProgressReporter | None = None,
    ) -> None:
        if generator.settings.domain != "project_management":
            raise ValueError(
                "ProjectManagementDatasetPipeline only supports "
                "project_management"
            )
        self.generator = generator
        self.settings = generator.settings
        self.progress = progress or ProgressReporter()
        self.exporter = ProjectManagementCSVExporter(
            generator,
            progress=self.progress,
        )
        self.row_caps = ProjectManagementRowCapValidator(generator)
        self.fk_validator = ProjectManagementDuckDBFKValidator(generator)
        self.join_validator = ProjectManagementJoinPathValidator(generator)
        self.imperfection_validator = ProjectManagementImperfectionRateValidator(
            generator
        )
        self.hash_computer = ProjectManagementHashComputer(generator)
        self.dictionary_generator = ProjectManagementDataDictionaryGenerator(generator)
        self.schema_generator = ProjectManagementSchemaSQLGenerator(generator)
        self.reproducibility_validator = ProjectManagementReproducibilityValidator(
            DeterministicGenerator(self.settings)
        )
        self.manifest_generator = ProjectManagementManifestGenerator(generator)

    @classmethod
    def for_profile(
        cls,
        profile: str,
        progress: ProgressReporter | None = None,
    ) -> "ProjectManagementDatasetPipeline":
        """Create a PM pipeline from validated profile settings."""

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
        self._step(1, total, "Validate PM configuration and expected row caps")
        validate_project_management_config()
        self._assert_gate(self.row_caps.validate_expected_counts())

        self._step(2, total, "Generate, validate, and export PM stage CSVs")
        self.exporter.export_dev_previews()
        final_dir = self.settings.output_path / "imperfect"

        self._step(3, total, "Validate persisted PM row caps")
        self._assert_gate(self.row_caps.validate_csv_directory(final_dir))

        self._step(
            4,
            total,
            "Validate persisted PM DDL, foreign keys, and assignments",
        )
        self._assert_gate(self.fk_validator.validate_csv_directory(final_dir))

        self._step(5, total, "Validate persisted PM join paths")
        self._assert_gate(self.join_validator.validate_csv_directory(final_dir))

        self._step(6, total, "Validate persisted PM imperfection rates")
        self._assert_gate(self.imperfection_validator.validate_csv_directory(final_dir))
        self.progress.report(f"PM dev dataset build complete: {final_dir}")
        return final_dir

    def _run_full(self) -> Path:
        total = 11
        self._step(1, total, "Validate PM configuration and expected row caps")
        validate_project_management_config()
        self._assert_gate(self.row_caps.validate_expected_counts())

        self._step(2, total, "Generate, validate, and export PM release CSVs")
        self.exporter.export_full_profile_csvs()
        release_dir = self.settings.output_path

        self._step(3, total, "Validate persisted PM release row caps")
        self._assert_gate(self.row_caps.validate_csv_directory(release_dir))

        self._step(
            4,
            total,
            "Validate persisted PM DDL, foreign keys, and assignments",
        )
        self._assert_gate(self.fk_validator.validate_csv_directory(release_dir))

        self._step(5, total, "Validate persisted PM join paths")
        self._assert_gate(self.join_validator.validate_csv_directory(release_dir))

        self._step(6, total, "Validate persisted PM imperfection rates")
        self._assert_gate(
            self.imperfection_validator.validate_csv_directory(release_dir)
        )

        self._step(7, total, "Compute PM release CSV SHA-256 hashes")
        self.hash_computer.compute_exported_csv_hashes()

        self._step(8, total, "Generate PM release data dictionary")
        self.dictionary_generator.write_release_dictionary()

        self._step(9, total, "Generate PM release schema SQL")
        self.schema_generator.write_release_schema()

        self._step(10, total, "Validate clean-room PM reproducibility")
        self._assert_gate(self.reproducibility_validator.validate_release())

        self._step(11, total, "Generate final immutable PM manifest")
        manifest_path = self.manifest_generator.write_manifest()
        self.progress.report(f"PM full release build complete: {release_dir}")
        return manifest_path

    def _step(self, number: int, total: int, message: str) -> None:
        self.progress.report(f"Step {number}/{total}: {message}")

    @staticmethod
    def _assert_gate(results: list[IntegrityCheckResult]) -> None:
        assert_all_passed(results)


__all__ = ["ProjectManagementDatasetPipeline"]
