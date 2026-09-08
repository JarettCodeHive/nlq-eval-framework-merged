"""CRM clean-room reproducibility validation."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from generators.common.base import DeterministicGenerator
from generators.common.csv_export import CSVExporter
from generators.common.integrity import IntegrityCheckResult
from generators.common.integrity import assert_all_passed
from generators.common.integrity import failed
from generators.common.integrity import passed
from generators.common.manifest import FileHash
from generators.common.manifest import compute_file_hashes
from generators.crm.config import settings_for_profile
from generators.crm.config import validate_crm_config
from generators.crm.hashes import CRMHashComputer
from generators.crm.imperfections import CRMImperfectionInjector
from generators.crm.integrity import CRMRelationalValidator


class CRMReproducibilityValidator:
    """Validate release CSVs are byte-identical to clean regeneration."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "crm":
            raise ValueError("CRMReproducibilityValidator only supports the crm domain")
        validate_crm_config()
        self.generator = generator
        self.settings = generator.settings

    @classmethod
    def for_profile(cls, profile: str) -> "CRMReproducibilityValidator":
        """Create a CRM reproducibility validator from config files."""

        settings = settings_for_profile(profile)
        return cls(DeterministicGenerator(settings))

    def validate_release(self) -> list[IntegrityCheckResult]:
        """Regenerate full CRM CSVs in temp storage and compare SHA-256 hashes."""

        if not self.settings.is_release_profile:
            raise ValueError(
                "Step 18 validates reproducibility only for the full profile; "
                f"got profile={self.settings.profile}"
            )

        release_hashes = CRMHashComputer(self.generator).compute_exported_csv_hashes()
        regenerated_hashes = self._regenerate_and_hash()
        return self.compare_hashes(release_hashes, regenerated_hashes)

    def validate_release_or_raise(self) -> list[IntegrityCheckResult]:
        """Validate reproducibility and raise on failure."""

        results = self.validate_release()
        assert_all_passed(results)
        return results

    def compare_hashes(
        self,
        release_hashes: list[FileHash],
        regenerated_hashes: list[FileHash],
    ) -> list[IntegrityCheckResult]:
        """Compare release and regenerated hashes in configured table order."""

        if len(release_hashes) != len(regenerated_hashes):
            return [
                failed(
                    "reproducibility.file_count",
                    "expected "
                    f"{len(release_hashes)} regenerated file(s), got "
                    f"{len(regenerated_hashes)}",
                )
            ]

        results: list[IntegrityCheckResult] = []
        for table_name, release_hash, regenerated_hash in zip(
            self.settings.table_order,
            release_hashes,
            regenerated_hashes,
            strict=True,
        ):
            check_name = f"{table_name}.reproducible_sha256"
            expected_name = f"{table_name}.csv"
            names_match = (
                release_hash.path.name == expected_name
                and regenerated_hash.path.name == expected_name
            )
            hashes_match = release_hash.sha256 == regenerated_hash.sha256
            sizes_match = release_hash.bytes == regenerated_hash.bytes
            if names_match and hashes_match and sizes_match:
                results.append(
                    passed(
                        check_name,
                        f"sha256={release_hash.sha256} bytes={release_hash.bytes}",
                    )
                )
            else:
                results.append(
                    failed(
                        check_name,
                        f"expected file={expected_name}; release "
                        f"file={release_hash.path.name} "
                        f"sha256={release_hash.sha256} bytes={release_hash.bytes}; "
                        "regenerated "
                        f"file={regenerated_hash.path.name} "
                        f"sha256={regenerated_hash.sha256} "
                        f"bytes={regenerated_hash.bytes}",
                    )
                )
        return results

    def _regenerate_and_hash(self) -> list[FileHash]:
        tables = CRMImperfectionInjector(self.generator).generate_imperfect_tables()
        relational_results = CRMRelationalValidator(self.generator).validate_tables(
            tables
        )
        assert_all_passed(relational_results)

        with TemporaryDirectory(prefix="crm-reproducibility-") as temp_dir:
            temp_path = Path(temp_dir)
            CSVExporter(self.settings.csv_format).export_tables(
                tables=tables,
                table_order=self.settings.table_order,
                output_dir=temp_path,
            )
            return compute_file_hashes(
                tuple(
                    temp_path / f"{table_name}.csv"
                    for table_name in self.settings.table_order
                )
            )
