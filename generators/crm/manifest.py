"""CRM release manifest generation."""

from __future__ import annotations

import csv
import platform
from importlib import metadata
from pathlib import Path
from typing import Any

from generators.common.base import PROJECT_ROOT
from generators.common.base import DeterministicGenerator
from generators.common.integrity import IntegrityCheckResult
from generators.common.manifest import FileHash
from generators.common.manifest import build_distribution_metadata
from generators.common.manifest import write_manifest
from generators.crm.config import load_crm_config
from generators.crm.config import settings_for_profile
from generators.crm.config import validate_crm_config
from generators.crm.fk_integrity import CRMDuckDBFKValidator
from generators.crm.hashes import CRMHashComputer
from generators.crm.imperfection_rates import CRMImperfectionRateValidator
from generators.crm.join_paths import CRMJoinPathValidator


class CRMManifestGenerator:
    """Generate immutable manifest.json for exported CRM release CSVs."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "crm":
            raise ValueError("CRMManifestGenerator only supports the crm domain")
        validate_crm_config()
        self.generator = generator
        self.settings = generator.settings
        self.crm_config = load_crm_config()

    @classmethod
    def for_profile(cls, profile: str) -> "CRMManifestGenerator":
        """Create a CRM manifest generator from config files."""

        settings = settings_for_profile(profile)
        return cls(DeterministicGenerator(settings))

    def generate_manifest(self) -> dict[str, Any]:
        """Build manifest content without writing it."""

        if not self.settings.is_release_profile:
            raise ValueError(
                "Step 18 generates manifest.json only for the full profile; "
                f"got profile={self.settings.profile}"
            )

        hashes = CRMHashComputer(self.generator).compute_exported_csv_hashes()
        csv_metadata = self._csv_metadata_by_table(hashes)
        validation_status = self._validation_status()
        if not validation_status["overall_passed"]:
            failed_checks = [
                f"{gate['name']}:{check['check_name']}"
                for gate in validation_status["gates"]
                for check in gate["checks"]
                if not check["passed"]
            ]
            raise ValueError(
                "Cannot generate manifest because validation failed: "
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
            "tables": csv_metadata,
            "hash_algorithm": "sha256",
            "hashes": {
                table_name: csv_metadata[table_name]["sha256"]
                for table_name in self.settings.table_order
            },
            "distributions": dict(self.settings.distributions),
            "distribution_configuration": {
                "shared_defaults_source": (
                    "config/generation/base.json:distribution_defaults"
                ),
                "domain_configuration_source": (
                    "config/generation/crm.json:distributions"
                ),
                "settings": build_distribution_metadata(
                    self.crm_config["distributions"],
                    self.settings.distributions,
                    self.crm_config["distribution_targets"],
                ),
            },
            "imperfections": dict(self.settings.imperfections),
            "domain_values": dict(self.crm_config["domain_values"]),
            "generation_rules": dict(self.crm_config["generation_rules"]),
            "business_mappings": dict(self.crm_config["business_mappings"]),
            "distribution_targets": dict(self.crm_config["distribution_targets"]),
            "imperfection_targets": dict(self.crm_config["imperfection_targets"]),
            "relationships": list(self.crm_config["relationships"]),
            "join_path_requirements": list(self.crm_config["join_path_requirements"]),
            "consistency_rules": list(self.crm_config["consistency_rules"]),
            "library_versions": _library_versions(),
            "validation_status": validation_status,
        }

    def write_manifest(self) -> Path:
        """Generate and write immutable release manifest.json."""

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
            self.settings.table_order, hashes, strict=True
        ):
            csv_metadata = _csv_metadata(file_hash.path)
            expected_columns = [
                field["name"]
                for field in self.crm_config["tables"][table_name]["fields"]
            ]
            if csv_metadata["columns"] != expected_columns:
                raise ValueError(
                    f"{table_name}.csv header differs from the configured schema: "
                    f"expected={expected_columns}, actual={csv_metadata['columns']}"
                )
            metadata_by_table[table_name] = {
                "file": file_hash.path.name,
                "path": _relative(file_hash.path),
                "role": self.crm_config["tables"][table_name]["role"],
                "primary_key": self.crm_config["tables"][table_name]["primary_key"],
                "configured_rows": self.settings.row_counts[table_name],
                "rows": csv_metadata["rows"],
                "columns": csv_metadata["columns"],
                "column_count": len(csv_metadata["columns"]),
                "fields": list(self.crm_config["tables"][table_name]["fields"]),
                "bytes": file_hash.bytes,
                "sha256": file_hash.sha256,
            }
        return metadata_by_table

    def _validation_status(self) -> dict[str, Any]:
        gates = [
            _gate_summary(
                "fk_integrity",
                CRMDuckDBFKValidator(self.generator).validate_exported_csvs(),
            ),
            _gate_summary(
                "join_paths",
                CRMJoinPathValidator(self.generator).validate_exported_csvs(),
            ),
            _gate_summary(
                "imperfection_rates",
                CRMImperfectionRateValidator(self.generator).validate_exported_csvs(),
            ),
        ]
        row_cap_gate = self._row_cap_gate()
        gates.insert(0, row_cap_gate)
        return {
            "overall_passed": all(gate["passed"] for gate in gates),
            "gates": gates,
        }

    def _row_cap_gate(self) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        cap = self.settings.max_rows_per_table
        csv_paths = CRMHashComputer(self.generator)._csv_paths(
            self.settings.output_path
        )
        for table_name, csv_path in zip(
            self.settings.table_order,
            csv_paths,
            strict=True,
        ):
            rows = _csv_metadata(csv_path)["rows"]
            passed = rows <= cap
            checks.append(
                {
                    "check_name": f"{table_name}.row_cap.exported",
                    "passed": passed,
                    "message": (
                        f"{rows} rows <= cap {cap}"
                        if passed
                        else f"{rows} rows exceeds cap {cap}"
                    ),
                }
            )
        return {
            "name": "row_caps",
            "passed": all(check["passed"] for check in checks),
            "checks": checks,
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
        "python": "",
    }
    versions = {"python": platform.python_version()}
    for key, package_name in packages.items():
        if key == "python":
            continue
        try:
            versions[key] = metadata.version(package_name)
        except metadata.PackageNotFoundError:
            versions[key] = "not_installed"
    return versions


def _relative(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))
