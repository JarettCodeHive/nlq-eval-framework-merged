"""Sales clean-room reproducibility validation."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from generators.core.base import DeterministicGenerator
from generators.core.csv_export import CSVExporter
from generators.core.integrity import IntegrityCheckResult
from generators.core.integrity import assert_all_passed
from generators.core.integrity import failed
from generators.core.integrity import passed
from generators.core.manifest import FileHash
from generators.core.manifest import compute_file_hashes
from generators.sales.config import settings_for_profile
from generators.sales.hashes import SalesHashComputer
from generators.sales.imperfections import SalesImperfectionInjector
from generators.sales.validators.config import validate_sales_config
from generators.sales.validators.relational import SalesRelationalValidator


@dataclass(frozen=True)
class SalesCSVFingerprint:
    """Observable identity and shape of one exported Sales CSV."""

    path: Path
    sha256: str
    bytes: int
    rows: int
    columns: tuple[str, ...]


class SalesReproducibilityValidator:
    """Compare a Sales release with a clean deterministic regeneration."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "sales":
            raise ValueError("SalesReproducibilityValidator only supports sales")
        validate_sales_config()
        self.generator = generator
        self.settings = generator.settings

    @classmethod
    def for_profile(cls, profile: str) -> "SalesReproducibilityValidator":
        """Create a Sales reproducibility validator from profile config."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def validate_release(self) -> list[IntegrityCheckResult]:
        """Regenerate in temporary storage and compare every CSV fingerprint."""

        if not self.settings.is_release_profile:
            raise ValueError(
                "Sales reproducibility validation requires the full profile; "
                f"got profile={self.settings.profile}"
            )
        release_hashes = SalesHashComputer(self.generator).compute_exported_csv_hashes()
        release_fingerprints = _fingerprints(release_hashes)
        regenerated_fingerprints = self._regenerate_and_fingerprint()
        return self.compare_fingerprints(
            release_fingerprints,
            regenerated_fingerprints,
        )

    def validate_release_or_raise(self) -> list[IntegrityCheckResult]:
        """Validate release reproducibility and raise on any mismatch."""

        results = self.validate_release()
        assert_all_passed(results)
        return results

    def compare_fingerprints(
        self,
        release: list[SalesCSVFingerprint],
        regenerated: list[SalesCSVFingerprint],
    ) -> list[IntegrityCheckResult]:
        """Compare file identity and shape in configured table order."""

        expected_count = len(self.settings.table_order)
        if len(release) != expected_count or len(regenerated) != expected_count:
            return [
                failed(
                    "reproducibility.file_count",
                    f"expected {expected_count} release and regenerated files; "
                    f"got release={len(release)}, regenerated={len(regenerated)}",
                )
            ]

        results: list[IntegrityCheckResult] = []
        for table_name, release_file, regenerated_file in zip(
            self.settings.table_order,
            release,
            regenerated,
            strict=True,
        ):
            expected_name = f"{table_name}.csv"
            matches = (
                release_file.path.name == expected_name
                and regenerated_file.path.name == expected_name
                and release_file.sha256 == regenerated_file.sha256
                and release_file.bytes == regenerated_file.bytes
                and release_file.rows == regenerated_file.rows
                and release_file.columns == regenerated_file.columns
            )
            check_name = f"{table_name}.reproducible_sha256"
            if matches:
                results.append(
                    passed(
                        check_name,
                        f"file={expected_name} sha256={release_file.sha256} "
                        f"bytes={release_file.bytes} rows={release_file.rows} "
                        f"columns={len(release_file.columns)}",
                    )
                )
            else:
                results.append(
                    failed(
                        check_name,
                        f"expected file={expected_name}; "
                        f"release={_fingerprint_text(release_file)}; "
                        f"regenerated={_fingerprint_text(regenerated_file)}",
                    )
                )
        return results

    def _regenerate_and_fingerprint(self) -> list[SalesCSVFingerprint]:
        # A fresh generator prevents earlier calls in this process from affecting
        # the clean-room comparison.
        clean_generator = DeterministicGenerator(self.settings)
        tables = SalesImperfectionInjector(clean_generator).generate_imperfect_tables()
        SalesRelationalValidator(clean_generator).validate_or_raise(tables)

        with TemporaryDirectory(prefix="sales-reproducibility-") as temp_dir:
            directory = Path(temp_dir)
            CSVExporter(self.settings.csv_format).export_tables(
                tables=tables,
                table_order=self.settings.table_order,
                output_dir=directory,
            )
            hashes = compute_file_hashes(
                tuple(
                    directory / f"{table_name}.csv"
                    for table_name in self.settings.table_order
                )
            )
            return _fingerprints(hashes)


def _fingerprints(file_hashes: list[FileHash]) -> list[SalesCSVFingerprint]:
    fingerprints: list[SalesCSVFingerprint] = []
    for file_hash in file_hashes:
        rows, columns = _csv_shape(file_hash.path)
        fingerprints.append(
            SalesCSVFingerprint(
                path=file_hash.path,
                sha256=file_hash.sha256,
                bytes=file_hash.bytes,
                rows=rows,
                columns=columns,
            )
        )
    return fingerprints


def _csv_shape(path: Path) -> tuple[int, tuple[str, ...]]:
    with path.open("r", encoding="utf-8", newline="") as csv_file:
        reader = csv.reader(csv_file)
        try:
            columns = tuple(next(reader))
        except StopIteration as exc:
            raise ValueError(f"CSV file is empty: {path}") from exc
        rows = sum(1 for _ in reader)
    return rows, columns


def _fingerprint_text(fingerprint: SalesCSVFingerprint) -> str:
    return (
        f"file={fingerprint.path.name} sha256={fingerprint.sha256} "
        f"bytes={fingerprint.bytes} rows={fingerprint.rows} "
        f"columns={list(fingerprint.columns)}"
    )


__all__ = ["SalesCSVFingerprint", "SalesReproducibilityValidator"]
