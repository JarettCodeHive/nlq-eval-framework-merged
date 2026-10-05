"""Logistics release manifest generation."""

from __future__ import annotations

from collections import Counter
import csv
from decimal import Decimal
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
from generators.logistics.config import load_logistics_config
from generators.logistics.config import settings_for_profile
from generators.logistics.data_dictionary import LogisticsDataDictionaryGenerator
from generators.logistics.hashes import LogisticsHashComputer
from generators.logistics.validators.config import validate_logistics_config
from generators.logistics.validators.fk_integrity import (
    LogisticsDuckDBFKValidator,
)
from generators.logistics.validators.fk_integrity import MANUAL_FK_CHECKS
from generators.logistics.validators.imperfection_rates import (
    LogisticsImperfectionRateValidator,
)
from generators.logistics.validators.join_paths import LogisticsJoinPathValidator
from generators.logistics.validators.row_caps import LogisticsRowCapValidator


DECLARED_ORPHAN_CHECKS = {
    "orders.warehouse_id.null_count",
    "orders.warehouse_id.declared_orphan_count",
    "orders.warehouse_id.orphan_namespace",
}


class LogisticsManifestGenerator:
    """Build and atomically seal a validated Logistics release."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "logistics":
            raise ValueError("LogisticsManifestGenerator only supports logistics")
        validate_logistics_config()
        self.generator = generator
        self.settings = generator.settings
        self.logistics_config = load_logistics_config()

    @classmethod
    def for_profile(cls, profile: str) -> "LogisticsManifestGenerator":
        """Create a Logistics manifest generator for a validated profile."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def generate_manifest(self) -> dict[str, Any]:
        """Build manifest content after all persisted release gates pass."""

        if not self.settings.is_release_profile:
            raise ValueError(
                "Logistics manifest generation requires the full profile; "
                f"got profile={self.settings.profile}"
            )

        hashes = LogisticsHashComputer(self.generator).compute_exported_csv_hashes()
        tables = self._csv_metadata_by_table(hashes)
        artifacts = self._release_artifact_metadata()
        validation_status = self._validation_status()
        if not validation_status["overall_passed"]:
            failed_checks = [
                f"{gate['name']}:{check['check_name']}"
                for gate in validation_status["gates"]
                for check in gate["checks"]
                if not check["passed"]
            ]
            raise ValueError(
                "Cannot generate Logistics manifest because validation failed: "
                + ", ".join(failed_checks[:10])
            )

        return {
            "manifest_schema_version": "1.0",
            "domain": self.settings.domain,
            "release_version": self.settings.release_version,
            "dataset_version": self.settings.dataset_version,
            "profile": self.settings.profile,
            "generated_at": self.settings.manifest_generated_at,
            "seed": self.settings.seed,
            "reference_today": self.settings.reference_today.isoformat(),
            "schema_source": _relative(self.settings.schema_source),
            "output_path": self._logical_release_path(),
            "table_order": list(self.settings.table_order),
            "csv_format": dict(self.settings.csv_format),
            "row_cap": self.settings.max_rows_per_table,
            "tables": tables,
            "hash_algorithm": "sha256",
            "hashes": {
                table_name: tables[table_name]["sha256"]
                for table_name in self.settings.table_order
            },
            "release_artifacts": artifacts,
            "distributions": dict(self.settings.distributions),
            "distribution_configuration": self._distribution_configuration(),
            "imperfections": dict(self.settings.imperfections),
            "imperfection_observations": self._imperfection_observations(),
            "domain_values": dict(self.logistics_config["domain_values"]),
            "generation_rules": dict(self.logistics_config["generation_rules"]),
            "business_mappings": dict(
                self.logistics_config["business_mappings"]
            ),
            "decimal_policy": dict(self.logistics_config["decimal_policy"]),
            "date_rules": list(self.logistics_config["date_rules"]),
            "decimal_rules": list(self.logistics_config["decimal_rules"]),
            "currency_rules": list(self.logistics_config["currency_rules"]),
            "imperfection_rules": list(
                self.logistics_config["imperfection_rules"]
            ),
            "distribution_targets": dict(
                self.logistics_config["distribution_targets"]
            ),
            "imperfection_targets": dict(
                self.logistics_config["imperfection_targets"]
            ),
            "relationships": list(self.logistics_config["relationships"]),
            "join_path_requirements": list(
                self.logistics_config["join_path_requirements"]
            ),
            "orphan_exclusions": list(
                self.logistics_config["orphan_exclusions"]
            ),
            "consistency_rules": list(
                self.logistics_config["consistency_rules"]
            ),
            "release_validation_rules": list(
                self.logistics_config["release_validation_rules"]
            ),
            "library_versions": _library_versions(),
            "validation_status": validation_status,
        }

    def write_manifest(self) -> Path:
        """Write manifest.json once, sealing the Logistics release."""

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
        tables: dict[str, dict[str, Any]] = {}
        for table_name, file_hash in zip(
            self.settings.table_order,
            hashes,
            strict=True,
        ):
            csv_metadata = _csv_metadata(file_hash.path)
            table_config = self.logistics_config["tables"][table_name]
            expected_columns = [field["name"] for field in table_config["fields"]]
            if csv_metadata["columns"] != expected_columns:
                raise ValueError(
                    f"{table_name}.csv header differs from the configured schema: "
                    f"expected={expected_columns}, "
                    f"actual={csv_metadata['columns']}"
                )
            tables[table_name] = {
                "file": file_hash.path.name,
                "path": self._logical_release_path(file_hash.path.name),
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
        return tables

    def _release_artifact_metadata(self) -> dict[str, dict[str, Any]]:
        release_schema = self.settings.output_path / "schema.sql"
        dictionary = self.settings.output_path / "data_dictionary.md"
        canonical_ddl = self.settings.schema_source
        erd = canonical_ddl.with_name("logistics_er.dbml")
        header_spec = canonical_ddl.with_name("logistics_csv_header_spec.md")
        semantic_contract = PROJECT_ROOT / self.logistics_config["semantic_contract"]
        required = (
            release_schema,
            dictionary,
            canonical_ddl,
            erd,
            header_spec,
            semantic_contract,
        )
        missing = [path for path in required if not path.is_file()]
        if missing:
            raise FileNotFoundError(
                "Logistics release support artifacts are missing. Generate "
                "schema SQL and the data dictionary before the manifest. "
                "Missing: " + ", ".join(str(path) for path in missing)
            )
        if release_schema.read_bytes() != canonical_ddl.read_bytes():
            raise ValueError(
                "Release schema.sql differs from canonical Logistics DDL"
            )
        expected_dictionary = (
            LogisticsDataDictionaryGenerator(self.generator)
            .generate_markdown()
            .encode("utf-8")
        )
        if dictionary.read_bytes() != expected_dictionary:
            raise ValueError(
                "Release data_dictionary.md differs from current Logistics config"
            )
        return {
            "schema_sql": _artifact_metadata(
                release_schema,
                logical_path=self._logical_release_path(release_schema.name),
            ),
            "data_dictionary": _artifact_metadata(
                dictionary,
                logical_path=self._logical_release_path(dictionary.name),
            ),
            "canonical_ddl": _artifact_metadata(canonical_ddl),
            "erd_dbml": _artifact_metadata(erd),
            "csv_header_spec": _artifact_metadata(header_spec),
            "semantic_contract": _artifact_metadata(semantic_contract),
        }

    def _logical_release_path(self, file_name: str | None = None) -> str:
        configured = settings_for_profile(self.settings.profile).output_path
        path = configured / file_name if file_name else configured
        return _relative(path)

    def _distribution_configuration(self) -> dict[str, Any]:
        return {
            "shared_defaults_source": (
                "config/generation/base.json:distribution_defaults"
            ),
            "domain_configuration_source": (
                "config/generation/logistics/generation.json:distributions"
            ),
            "settings": build_distribution_metadata(
                self.logistics_config["distributions"],
                self.settings.distributions,
                self.logistics_config["distribution_targets"],
            ),
        }

    def _validation_status(self) -> dict[str, Any]:
        # Regeneration-based gates use independent contexts so RNG consumption
        # and validation order cannot change results.
        row_results = LogisticsRowCapValidator(
            self._fresh_generator()
        ).validate_exported_csvs()
        fk_results = LogisticsDuckDBFKValidator(
            self._fresh_generator()
        ).validate_exported_csvs()
        join_results = LogisticsJoinPathValidator(
            self._fresh_generator()
        ).validate_exported_csvs()
        imperfection_results = LogisticsImperfectionRateValidator(
            self._fresh_generator()
        ).validate_exported_csvs()
        gates = [
            _gate_summary("row_caps", row_results),
            _gate_summary("fk_integrity", fk_results),
            _gate_summary("join_paths", join_results),
            _gate_summary("imperfection_rates", imperfection_results),
        ]
        return {
            "overall_passed": all(gate["passed"] for gate in gates),
            "gates": gates,
            "capabilities": {
                "row_caps": _all_passed(row_results),
                "physical_fk_integrity": _named_checks_passed(
                    fk_results, set(MANUAL_FK_CHECKS)
                ),
                "declared_orphan_policy": _named_checks_passed(
                    fk_results, DECLARED_ORPHAN_CHECKS
                ),
                "join_paths": _all_passed(join_results),
                "imperfection_rates": _all_passed(imperfection_results),
                "reproducibility": {
                    "status": "pending_step_24",
                    "passed": None,
                    "message": (
                        "Clean-room byte comparison is performed by the separate "
                        "Logistics reproducibility validator."
                    ),
                },
            },
        }

    def _fresh_generator(self) -> DeterministicGenerator:
        return DeterministicGenerator(self.settings)

    def _imperfection_observations(self) -> dict[str, Any]:
        output = self.settings.output_path
        warehouse_rows = _read_csv_rows(output / "warehouses.csv")
        order_rows = _read_csv_rows(output / "orders.csv")
        shipment_rows = _read_csv_rows(output / "shipments.csv")
        validator = LogisticsImperfectionRateValidator(self._fresh_generator())

        duplicate_target = self.logistics_config["imperfection_targets"][
            "near_duplicate_shipments"
        ]
        business_keys = duplicate_target["business_key_fields"]
        shipment_counts = Counter(
            tuple(row[field] for field in business_keys) for row in shipment_rows
        )
        duplicate_count = sum(count - 1 for count in shipment_counts.values())

        warehouse_ids = {row["warehouse_id"] for row in warehouse_rows}
        null_warehouse_count = sum(
            row["warehouse_id"] == "" for row in order_rows
        )
        orphan_rows = [
            row
            for row in order_rows
            if row["warehouse_id"]
            and row["warehouse_id"] not in warehouse_ids
        ]
        outlier_minimum = Decimal(
            str(
                self.logistics_config["imperfection_targets"][
                    "order_total_outliers"
                ]["minimum_value"]
            )
        )
        outlier_count = sum(
            Decimal(row["total_amount"]) >= outlier_minimum for row in order_rows
        )

        boundaries = list(self.settings.imperfections["boundary_dates"])
        table_rows = {"orders": order_rows, "shipments": shipment_rows}
        boundary_target = self.logistics_config["imperfection_targets"][
            "coordinated_boundary_dates"
        ]
        boundary_counts: dict[str, dict[str, int]] = {}
        for qualified in [
            boundary_target["primary_target"],
            *boundary_target["dependent_targets"],
        ]:
            table_name, field_name = qualified.split(".", 1)
            values = Counter(
                row[field_name].split("T", 1)[0]
                for row in table_rows[table_name]
                if row[field_name]
            )
            boundary_counts[qualified] = {
                value: values[value] for value in boundaries
            }

        return {
            "near_duplicate_shipments": {
                "expected": validator.expected_duplicate_count(),
                "observed": duplicate_count,
                "physical_rows": len(shipment_rows),
                "distinct_business_shipments": len(shipment_counts),
            },
            "missing_order_warehouses": {
                "expected": validator.expected_null_count(),
                "observed": null_warehouse_count,
            },
            "orphaned_order_warehouses": {
                "expected": validator.expected_orphan_count(),
                "observed": len(orphan_rows),
            },
            "order_total_outliers": {
                "expected": validator.expected_outlier_count(),
                "observed": outlier_count,
            },
            "coordinated_boundary_dates": {
                "expected": boundaries,
                "observed_counts": boundary_counts,
            },
        }


def _artifact_metadata(
    path: Path,
    logical_path: str | None = None,
) -> dict[str, Any]:
    file_hash = compute_sha256(path)
    return {
        "file": path.name,
        "path": logical_path or _relative(path),
        "bytes": file_hash.bytes,
        "sha256": file_hash.sha256,
    }


def _gate_summary(
    name: str,
    results: list[IntegrityCheckResult],
) -> dict[str, Any]:
    return {
        "name": name,
        "passed": _all_passed(results),
        "checks": [
            {
                "check_name": result.check_name,
                "passed": result.passed,
                "message": result.message,
            }
            for result in results
        ],
    }


def _all_passed(results: list[IntegrityCheckResult]) -> bool:
    return bool(results) and all(result.passed for result in results)


def _named_checks_passed(
    results: list[IntegrityCheckResult],
    names: set[str],
) -> bool:
    matching = [result for result in results if result.check_name in names]
    return len(matching) == len(names) and _all_passed(matching)


def _csv_metadata(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8", newline="") as csv_file:
        reader = csv.reader(csv_file)
        try:
            columns = next(reader)
        except StopIteration as exc:
            raise ValueError(f"CSV file is empty: {path}") from exc
        rows = sum(1 for _ in reader)
    return {"columns": columns, "rows": rows}


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


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


__all__ = ["LogisticsManifestGenerator"]
