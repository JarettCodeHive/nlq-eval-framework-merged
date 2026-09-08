"""CRM exported CSV SHA-256 hashing."""

from __future__ import annotations

from pathlib import Path

from generators.common.base import DeterministicGenerator
from generators.common.manifest import FileHash
from generators.common.manifest import compute_file_hashes
from generators.crm.config import settings_for_profile
from generators.crm.config import validate_crm_config


class CRMHashComputer:
    """Compute SHA-256 hashes for exported CRM CSV files."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "crm":
            raise ValueError("CRMHashComputer only supports the crm domain")
        validate_crm_config()
        self.generator = generator
        self.settings = generator.settings

    @classmethod
    def for_profile(cls, profile: str) -> "CRMHashComputer":
        """Create a CRM hash computer from config files."""

        settings = settings_for_profile(profile)
        return cls(DeterministicGenerator(settings))

    def compute_exported_csv_hashes(self) -> list[FileHash]:
        """Compute hashes for CSVs in the configured output path."""

        if not self.settings.is_release_profile:
            raise ValueError(
                "Step 18 computes release CSV hashes only for the full profile; "
                f"got profile={self.settings.profile}"
            )
        return compute_file_hashes(self._csv_paths(self.settings.output_path))

    def _csv_paths(self, directory: Path) -> tuple[Path, ...]:
        csv_paths = tuple(
            directory / f"{table_name}.csv" for table_name in self.settings.table_order
        )
        missing = [path for path in csv_paths if not path.exists()]
        if missing:
            missing_text = ", ".join(str(path) for path in missing)
            raise FileNotFoundError(
                "CRM release CSVs are missing. Run "
                "`python main.py export-csvs --profile full` before computing "
                f"SHA-256 hashes. Missing: {missing_text}"
            )
        expected_names = {path.name for path in csv_paths}
        unexpected = sorted(
            path for path in directory.glob("*.csv") if path.name not in expected_names
        )
        if unexpected:
            unexpected_text = ", ".join(str(path) for path in unexpected)
            raise ValueError(
                "CRM release contains CSVs outside the configured table contract: "
                f"{unexpected_text}. Re-run `python main.py export-csvs "
                "--profile full` to rebuild the release CSV inventory."
            )
        return csv_paths
