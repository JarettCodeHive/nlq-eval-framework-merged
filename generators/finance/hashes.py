"""Finance exported CSV SHA-256 hashing."""

from __future__ import annotations

from pathlib import Path

from generators.core.base import DeterministicGenerator
from generators.core.manifest import FileHash
from generators.core.manifest import compute_file_hashes
from generators.finance.config import settings_for_profile
from generators.finance.validators.config import validate_finance_config


class FinanceHashComputer:
    """Compute raw-byte SHA-256 metadata for exported Finance CSV files."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "finance":
            raise ValueError("FinanceHashComputer only supports finance")
        validate_finance_config()
        self.generator = generator
        self.settings = generator.settings

    @classmethod
    def for_profile(cls, profile: str) -> "FinanceHashComputer":
        """Create a Finance hash computer from validated profile config."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def compute_exported_csv_hashes(self) -> list[FileHash]:
        """Hash full-profile Finance CSV bytes in configured table order."""

        if not self.settings.is_release_profile:
            raise ValueError(
                "Finance release CSV hashing requires the full profile; "
                f"got profile={self.settings.profile}"
            )
        return compute_file_hashes(self._csv_paths(self.settings.output_path))

    def _csv_paths(self, directory: Path) -> tuple[Path, ...]:
        csv_paths = tuple(
            directory / f"{table_name}.csv"
            for table_name in self.settings.table_order
        )
        missing = [path for path in csv_paths if not path.exists()]
        if missing:
            raise FileNotFoundError(
                "Finance release CSVs are missing. Export the full profile before "
                "computing SHA-256 hashes. Missing: "
                + ", ".join(str(path) for path in missing)
            )

        expected_names = {path.name for path in csv_paths}
        unexpected = sorted(
            path for path in directory.glob("*.csv") if path.name not in expected_names
        )
        if unexpected:
            raise ValueError(
                "Finance release contains CSVs outside the configured table "
                "contract: "
                + ", ".join(str(path) for path in unexpected)
            )
        return csv_paths


__all__ = ["FinanceHashComputer"]
