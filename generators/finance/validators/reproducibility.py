"""Finance clean-room release reproducibility validation."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.core.integrity import IntegrityCheckResult
from generators.core.integrity import assert_all_passed
from generators.core.integrity import failed
from generators.core.integrity import passed
from generators.core.manifest import compute_sha256
from generators.finance.config import settings_for_profile
from generators.finance.data_dictionary import FinanceDataDictionaryGenerator
from generators.finance.export import FinanceCSVExporter
from generators.finance.manifest import FinanceManifestGenerator
from generators.finance.schema_sql import FinanceSchemaSQLGenerator
from generators.finance.validators.config import validate_finance_config


@dataclass(frozen=True)
class FinanceArtifactFingerprint:
    """Observable identity and optional tabular shape of one release artifact."""

    name: str
    sha256: str
    bytes: int
    rows: int | None = None
    columns: tuple[str, ...] | None = None


@dataclass(frozen=True)
class FinanceReleaseSnapshot:
    """Complete comparable evidence from one isolated Finance release build."""

    artifacts: tuple[FinanceArtifactFingerprint, ...]
    manifest: dict[str, Any]
    validation_status: dict[str, Any]


class FinanceReproducibilityValidator:
    """Compare two independently generated, fully validated Finance releases."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "finance":
            raise ValueError("FinanceReproducibilityValidator only supports finance")
        validate_finance_config()
        self.generator = generator
        self.settings = generator.settings

    @classmethod
    def for_profile(cls, profile: str) -> "FinanceReproducibilityValidator":
        """Create a Finance reproducibility validator from profile config."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def validate_release(self) -> list[IntegrityCheckResult]:
        """Build two isolated full releases and compare every observable byte."""

        if not self.settings.is_release_profile:
            raise ValueError(
                "Finance reproducibility validation requires the full profile; "
                f"got profile={self.settings.profile}"
            )

        with TemporaryDirectory(prefix="finance-reproducibility-a-") as first_dir:
            with TemporaryDirectory(
                prefix="finance-reproducibility-b-"
            ) as second_dir:
                first = self._build_snapshot(Path(first_dir))
                second = self._build_snapshot(Path(second_dir))
        return self.compare_snapshots(first, second)

    def validate_release_or_raise(self) -> list[IntegrityCheckResult]:
        """Validate clean-room reproducibility and raise on any mismatch."""

        results = self.validate_release()
        assert_all_passed(results)
        return results

    def compare_snapshots(
        self,
        first: FinanceReleaseSnapshot,
        second: FinanceReleaseSnapshot,
    ) -> list[IntegrityCheckResult]:
        """Compare ordered artifacts, full manifests, and validation outcomes."""

        expected_names = self._expected_artifact_names()
        if len(first.artifacts) != len(expected_names) or len(
            second.artifacts
        ) != len(expected_names):
            return [
                failed(
                    "reproducibility.file_count",
                    f"expected {len(expected_names)} artifacts in each build; "
                    f"got first={len(first.artifacts)}, "
                    f"second={len(second.artifacts)}",
                )
            ]

        results: list[IntegrityCheckResult] = []
        for expected_name, first_file, second_file in zip(
            expected_names,
            first.artifacts,
            second.artifacts,
            strict=True,
        ):
            matches = (
                first_file.name == expected_name
                and second_file.name == expected_name
                and first_file.sha256 == second_file.sha256
                and first_file.bytes == second_file.bytes
                and first_file.rows == second_file.rows
                and first_file.columns == second_file.columns
            )
            check_name = f"{_artifact_id(expected_name)}.reproducible_sha256"
            if matches:
                results.append(
                    passed(
                        check_name,
                        f"file={expected_name} sha256={first_file.sha256} "
                        f"bytes={first_file.bytes} rows={first_file.rows} "
                        f"columns={_column_count(first_file.columns)}",
                    )
                )
            else:
                results.append(
                    failed(
                        check_name,
                        f"expected file={expected_name}; "
                        f"first={_fingerprint_text(first_file)}; "
                        f"second={_fingerprint_text(second_file)}",
                    )
                )

        results.append(
            _equality_result(
                "manifest.reproducible_payload",
                first.manifest,
                second.manifest,
                "complete manifest payloads are identical",
            )
        )
        results.append(
            _equality_result(
                "validation.reproducible_outcomes",
                first.validation_status,
                second.validation_status,
                "accounting, FX, join, row-cap, and imperfection outcomes match",
            )
        )
        return results

    def _build_snapshot(self, directory: Path) -> FinanceReleaseSnapshot:
        """Generate, validate, and seal one isolated full Finance release."""

        clean_settings = settings_for_profile(self.settings.profile)
        clean_settings = _with_output_path(clean_settings, directory)
        clean_generator = DeterministicGenerator(clean_settings)

        FinanceCSVExporter(clean_generator).export_full_profile_csvs()
        FinanceSchemaSQLGenerator(clean_generator).write_release_schema()
        FinanceDataDictionaryGenerator(clean_generator).write_release_dictionary()
        manifest_path = FinanceManifestGenerator(clean_generator).write_manifest()

        artifact_paths = [
            *(directory / f"{name}.csv" for name in self.settings.table_order),
            directory / "schema.sql",
            directory / "data_dictionary.md",
            manifest_path,
        ]
        artifacts = tuple(
            _fingerprint(path, is_csv=path.suffix == ".csv")
            for path in artifact_paths
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return FinanceReleaseSnapshot(
            artifacts=artifacts,
            manifest=manifest,
            validation_status=manifest["validation_status"],
        )

    def _expected_artifact_names(self) -> tuple[str, ...]:
        return (
            *(f"{name}.csv" for name in self.settings.table_order),
            "schema.sql",
            "data_dictionary.md",
            "manifest.json",
        )


def _with_output_path(
    settings: GenerationSettings,
    output_path: Path,
) -> GenerationSettings:
    return replace(settings, output_path=output_path)


def _fingerprint(path: Path, is_csv: bool) -> FinanceArtifactFingerprint:
    file_hash = compute_sha256(path)
    rows: int | None = None
    columns: tuple[str, ...] | None = None
    if is_csv:
        rows, columns = _csv_shape(path)
    return FinanceArtifactFingerprint(
        name=path.name,
        sha256=file_hash.sha256,
        bytes=file_hash.bytes,
        rows=rows,
        columns=columns,
    )


def _csv_shape(path: Path) -> tuple[int, tuple[str, ...]]:
    with path.open("r", encoding="utf-8", newline="") as csv_file:
        reader = csv.reader(csv_file)
        try:
            columns = tuple(next(reader))
        except StopIteration as exc:
            raise ValueError(f"CSV file is empty: {path}") from exc
        rows = sum(1 for _ in reader)
    return rows, columns


def _equality_result(
    check_name: str,
    first: Any,
    second: Any,
    success_message: str,
) -> IntegrityCheckResult:
    if first == second:
        return passed(check_name, success_message)
    return failed(check_name, "first and second clean-room values differ")


def _artifact_id(file_name: str) -> str:
    return file_name.replace(".", "_")


def _column_count(columns: tuple[str, ...] | None) -> int | str:
    return len(columns) if columns is not None else "n/a"


def _fingerprint_text(fingerprint: FinanceArtifactFingerprint) -> str:
    return (
        f"file={fingerprint.name} sha256={fingerprint.sha256} "
        f"bytes={fingerprint.bytes} rows={fingerprint.rows} "
        f"columns={fingerprint.columns}"
    )


__all__ = [
    "FinanceArtifactFingerprint",
    "FinanceReleaseSnapshot",
    "FinanceReproducibilityValidator",
]
