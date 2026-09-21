"""Sales release manifest generation."""

from __future__ import annotations

import csv
from importlib import metadata
import platform
from pathlib import Path
from typing import Any

from generators.core.base import PROJECT_ROOT
from generators.core.base import DeterministicGenerator
from generators.core.integrity import IntegrityCheckResult
from generators.core.manifest import FileHash
from generators.core.manifest import build_distribution_metadata
from generators.core.manifest import compute_sha256
from generators.core.manifest import write_manifest
from generators.sales.config import load_sales_config
from generators.sales.config import settings_for_profile
from generators.sales.data_dictionary import SalesDataDictionaryGenerator
from generators.sales.hashes import SalesHashComputer
from generators.sales.validators.config import validate_sales_config
from generators.sales.validators.fk_integrity import SalesDuckDBFKValidator
from generators.sales.validators.imperfection_rates import (
    SalesImperfectionRateValidator,
)
from generators.sales.validators.join_paths import SalesJoinPathValidator
from generators.sales.validators.row_caps import SalesRowCapValidator


class SalesManifestGenerator:
    """Build and atomically seal a validated Sales release manifest."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "sales":
            raise ValueError("SalesManifestGenerator only supports sales")
        validate_sales_config()
        self.generator = generator
        self.settings = generator.settings
        self.sales_config = load_sales_config()

    @classmethod
    def for_profile(cls, profile: str) -> "SalesManifestGenerator":
        """Create a Sales manifest generator from validated profile config."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def generate_manifest(self) -> dict[str, Any]:
        """Build manifest content after every Sales release gate passes."""

        if not self.settings.is_release_profile:
            raise ValueError(
                "Sales manifest generation requires the full profile; "
                f"got profile={self.settings.profile}"
            )

        hashes = SalesHashComputer(self.generator).compute_exported_csv_hashes()
        table_metadata = self._csv_metadata_by_table(hashes)
        release_artifacts = self._release_artifact_metadata()
        validation_status = self._validation_status()
        if not validation_status["overall_passed"]:
            failed_checks = [
                f"{gate['name']}:{check['check_name']}"
                for gate in validation_status["gates"]
                for check in gate["checks"]
                if not check["passed"]
            ]
            raise ValueError(
                "Cannot generate Sales manifest because validation failed: "
                + ", ".join(failed_checks[:10])
            )

        return {
            "manifest_schema_version": "1.0",
            "domain": self.settings.domain,
            "dataset_version": self.settings.dataset_version,
            "profile": self.settings.profile,
            "generated_at": self.settings.manifest_generated_at,
            "seed": self.settings.seed,
            "reference_today": self.settings.reference_today.isoformat(),
            "schema_source": _relative(self.settings.schema_source),
            "output_path": _relative(self.settings.output_path),
            "table_order": list(self.settings.table_order),
            "csv_format": dict(self.settings.csv_format),
            "row_cap": self.settings.max_rows_per_table,
            "tables": table_metadata,
            "hash_algorithm": "sha256",
            "hashes": {
                table_name: table_metadata[table_name]["sha256"]
                for table_name in self.settings.table_order
            },
            "release_artifacts": release_artifacts,
            "distributions": dict(self.settings.distributions),
            "distribution_configuration": {
                "shared_defaults_source": (
                    "config/generation/base.json:distribution_defaults"
                ),
                "domain_configuration_source": (
                    "config/generation/sales.json:distributions"
                ),
                "settings": build_distribution_metadata(
                    self.sales_config["distributions"],
                    self.settings.distributions,
                    self.sales_config["distribution_targets"],
                ),
            },
            "imperfections": dict(self.settings.imperfections),
            "domain_values": dict(self.sales_config["domain_values"]),
            "generation_rules": dict(self.sales_config["generation_rules"]),
            "business_mappings": dict(self.sales_config["business_mappings"]),
            "distribution_targets": dict(self.sales_config["distribution_targets"]),
            "imperfection_targets": dict(self.sales_config["imperfection_targets"]),
            "relationships": list(self.sales_config["relationships"]),
            "join_path_requirements": list(self.sales_config["join_path_requirements"]),
            "consistency_rules": list(self.sales_config["consistency_rules"]),
            "library_versions": _library_versions(),
            "validation_status": validation_status,
        }

    def write_manifest(self) -> Path:
        """Write manifest.json once, sealing the validated Sales release."""

        manifest_path = self.settings.output_path / "manifest.json"
        if manifest_path.exists():
            raise FileExistsError(
                f"Refusing to overwrite immutable release manifest: {manifest_path}"
            )
        write_manifest(manifest_path, self.generate_manifest())
        return manifest_path

    def _csv_metadata_by_table(
        self,
        hashes: list[FileHash],
    ) -> dict[str, dict[str, Any]]:
        metadata_by_table: dict[str, dict[str, Any]] = {}
        for table_name, file_hash in zip(
            self.settings.table_order,
            hashes,
            strict=True,
        ):
            csv_metadata = _csv_metadata(file_hash.path)
            table_config = self.sales_config["tables"][table_name]
            expected_columns = [field["name"] for field in table_config["fields"]]
            if csv_metadata["columns"] != expected_columns:
                raise ValueError(
                    f"{table_name}.csv header differs from the configured schema: "
                    f"expected={expected_columns}, "
                    f"actual={csv_metadata['columns']}"
                )
            metadata_by_table[table_name] = {
                "file": file_hash.path.name,
                "path": _relative(file_hash.path),
                "role": table_config["role"],
                "primary_key": table_config["primary_key"],
                "configured_rows": self.settings.row_counts[table_name],
                "rows": csv_metadata["rows"],
                "columns": csv_metadata["columns"],
                "column_count": len(csv_metadata["columns"]),
                "fields": list(table_config["fields"]),
                "bytes": file_hash.bytes,
                "sha256": file_hash.sha256,
            }
        return metadata_by_table

    def _release_artifact_metadata(self) -> dict[str, dict[str, Any]]:
        schema_path = self.settings.output_path / "schema.sql"
        dictionary_path = self.settings.output_path / "data_dictionary.md"
        missing = [
            path for path in (schema_path, dictionary_path) if not path.is_file()
        ]
        if missing:
            raise FileNotFoundError(
                "Sales release support artifacts are missing. Generate schema SQL "
                "and the data dictionary before the manifest. Missing: "
                + ", ".join(str(path) for path in missing)
            )

        canonical_schema = self.settings.schema_source.read_bytes()
        if schema_path.read_bytes() != canonical_schema:
            raise ValueError("Release schema.sql differs from canonical Sales DDL")
        expected_dictionary = (
            SalesDataDictionaryGenerator(self.generator)
            .generate_markdown()
            .encode("utf-8")
        )
        if dictionary_path.read_bytes() != expected_dictionary:
            raise ValueError(
                "Release data_dictionary.md differs from current Sales config"
            )

        return {
            "schema_sql": _artifact_metadata(schema_path),
            "data_dictionary": _artifact_metadata(dictionary_path),
        }

    def _validation_status(self) -> dict[str, Any]:
        gates = [
            _gate_summary(
                "row_caps",
                SalesRowCapValidator(self.generator).validate_exported_csvs(),
            ),
            _gate_summary(
                "fk_integrity",
                SalesDuckDBFKValidator(self.generator).validate_exported_csvs(),
            ),
            _gate_summary(
                "join_paths",
                SalesJoinPathValidator(self.generator).validate_exported_csvs(),
            ),
            _gate_summary(
                "imperfection_rates",
                SalesImperfectionRateValidator(self.generator).validate_exported_csvs(),
            ),
        ]
        return {
            "overall_passed": all(gate["passed"] for gate in gates),
            "gates": gates,
        }


def _artifact_metadata(path: Path) -> dict[str, Any]:
    file_hash = compute_sha256(path)
    return {
        "file": path.name,
        "path": _relative(path),
        "bytes": file_hash.bytes,
        "sha256": file_hash.sha256,
    }


def _gate_summary(
    name: str,
    results: list[IntegrityCheckResult],
) -> dict[str, Any]:
    return {
        "name": name,
        "passed": all(result.passed for result in results),
        "checks": [
            {
                "check_name": result.check_name,
                "passed": result.passed,
                "message": result.message,
            }
            for result in results
        ],
    }


def _csv_metadata(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8", newline="") as csv_file:
        reader = csv.reader(csv_file)
        try:
            columns = next(reader)
        except StopIteration as exc:
            raise ValueError(f"CSV file is empty: {path}") from exc
        rows = sum(1 for _ in reader)
    return {"columns": columns, "rows": rows}


def _library_versions() -> dict[str, str]:
    packages = {
        "duckdb": "duckdb",
        "faker": "Faker",
        "numpy": "numpy",
        "pandas": "pandas",
    }
    versions = {"python": platform.python_version()}
    for key, package_name in packages.items():
        try:
            versions[key] = metadata.version(package_name)
        except metadata.PackageNotFoundError:
            versions[key] = "not_installed"
    return versions


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


__all__ = ["SalesManifestGenerator"]
