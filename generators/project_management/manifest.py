"""Project Management release manifest generation."""

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
from generators.project_management.config import load_project_management_config
from generators.project_management.config import settings_for_profile
from generators.project_management.data_dictionary import (
    ProjectManagementDataDictionaryGenerator,
)
from generators.project_management.hashes import ProjectManagementHashComputer
from generators.project_management.validators.config import (
    validate_project_management_config,
)
from generators.project_management.validators.fk_integrity import MANUAL_FK_CHECKS
from generators.project_management.validators.fk_integrity import (
    SEMANTIC_RELATIONSHIP_CHECKS,
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
from generators.project_management.validators.row_caps import (
    ProjectManagementRowCapValidator,
)


class ProjectManagementManifestGenerator:
    """Build and atomically seal a validated Project Management release."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "project_management":
            raise ValueError(
                "ProjectManagementManifestGenerator only supports "
                "project_management"
            )
        validate_project_management_config()
        self.generator = generator
        self.settings = generator.settings
        self.pm_config = load_project_management_config()

    @classmethod
    def for_profile(cls, profile: str) -> "ProjectManagementManifestGenerator":
        """Create a PM manifest generator from validated profile config."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def generate_manifest(self) -> dict[str, Any]:
        """Build manifest content after all currently available gates pass."""

        if not self.settings.is_release_profile:
            raise ValueError(
                "Project Management manifest generation requires the full "
                f"profile; got profile={self.settings.profile}"
            )

        hashes = ProjectManagementHashComputer(
            self.generator
        ).compute_exported_csv_hashes()
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
                "Cannot generate Project Management manifest because validation "
                "failed: " + ", ".join(failed_checks[:10])
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
            "domain_values": dict(self.pm_config["domain_values"]),
            "generation_rules": dict(self.pm_config["generation_rules"]),
            "business_mappings": dict(self.pm_config["business_mappings"]),
            "date_rules": list(self.pm_config["date_rules"]),
            "decimal_policy": dict(self.pm_config["decimal_policy"]),
            "decimal_rules": list(self.pm_config["decimal_rules"]),
            "currency_rules": list(self.pm_config["currency_rules"]),
            "semantic_contract": list(self.pm_config["semantic_contract"]),
            "distribution_targets": dict(
                self.pm_config["distribution_targets"]
            ),
            "imperfection_targets": dict(
                self.pm_config["imperfection_targets"]
            ),
            "relationships": list(self.pm_config["relationships"]),
            "join_path_requirements": list(
                self.pm_config["join_path_requirements"]
            ),
            "consistency_rules": list(self.pm_config["consistency_rules"]),
            "library_versions": _library_versions(),
            "validation_status": validation_status,
        }

    def write_manifest(self) -> Path:
        """Write manifest.json once, making the PM release immutable."""

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
            table_config = self.pm_config["tables"][table_name]
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
        erd = canonical_ddl.with_name("project_management_er.dbml")
        header_spec = canonical_ddl.with_name(
            "project_management_csv_header_spec.md"
        )
        required = (release_schema, dictionary, canonical_ddl, erd, header_spec)
        missing = [path for path in required if not path.is_file()]
        if missing:
            raise FileNotFoundError(
                "Project Management release support artifacts are missing. "
                "Generate schema SQL and the data dictionary before the manifest. "
                "Missing: " + ", ".join(str(path) for path in missing)
            )
        if release_schema.read_bytes() != canonical_ddl.read_bytes():
            raise ValueError(
                "Release schema.sql differs from canonical Project Management DDL"
            )
        expected_dictionary = (
            ProjectManagementDataDictionaryGenerator(self.generator)
            .generate_markdown()
            .encode("utf-8")
        )
        if dictionary.read_bytes() != expected_dictionary:
            raise ValueError(
                "Release data_dictionary.md differs from current Project "
                "Management config"
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
                "config/generation/project_management/generation.json:distributions"
            ),
            "settings": build_distribution_metadata(
                self.pm_config["distributions"],
                self.settings.distributions,
                self.pm_config["distribution_targets"],
            ),
        }

    def _validation_status(self) -> dict[str, Any]:
        # Each gate receives an unconsumed context so RNG use remains isolated.
        row_results = ProjectManagementRowCapValidator(
            self._fresh_generator()
        ).validate_exported_csvs()
        fk_results = ProjectManagementDuckDBFKValidator(
            self._fresh_generator()
        ).validate_exported_csvs()
        join_results = ProjectManagementJoinPathValidator(
            self._fresh_generator()
        ).validate_exported_csvs()
        imperfection_results = ProjectManagementImperfectionRateValidator(
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
                "semantic_assignment_membership": _named_checks_passed(
                    fk_results, set(SEMANTIC_RELATIONSHIP_CHECKS)
                ),
                "join_paths": _all_passed(join_results),
                "imperfection_rates": _all_passed(imperfection_results),
                "reproducibility": {
                    "status": "pending_step_24",
                    "passed": None,
                    "message": (
                        "Clean-room byte comparison is performed by the separate "
                        "Project Management reproducibility validator."
                    ),
                },
            },
        }

    def _fresh_generator(self) -> DeterministicGenerator:
        return DeterministicGenerator(self.settings)

    def _imperfection_observations(self) -> dict[str, Any]:
        output = self.settings.output_path
        projects = _read_csv_rows(output / "projects.csv")
        tasks = _read_csv_rows(output / "tasks.csv")
        time_entries = _read_csv_rows(output / "time_entries.csv")
        validator = ProjectManagementImperfectionRateValidator(
            self._fresh_generator()
        )

        duplicate_target = self.pm_config["imperfection_targets"][
            "near_duplicate_time_entries"
        ]
        business_keys = duplicate_target["business_key_fields"]
        counts = Counter(
            tuple(row[field] for field in business_keys) for row in time_entries
        )
        duplicate_count = sum(count - 1 for count in counts.values())
        minimum = Decimal(
            str(
                self.pm_config["imperfection_targets"][
                    "task_estimate_outliers"
                ]["minimum_value"]
            )
        )
        outlier_count = sum(
            Decimal(row["estimate_hours"]) >= minimum
            for row in tasks
            if row["estimate_hours"]
        )
        boundaries = list(self.settings.imperfections["boundary_dates"])
        boundary_counts: dict[str, dict[str, int]] = {}
        table_rows = {
            "projects": projects,
            "tasks": tasks,
            "milestones": _read_csv_rows(output / "milestones.csv"),
            "time_entries": time_entries,
        }
        for qualified in self.pm_config["imperfection_targets"][
            "coordinated_boundary_dates"
        ]["targets"]:
            table_name, field_name = qualified.split(".", 1)
            values = Counter(row[field_name] for row in table_rows[table_name])
            boundary_counts[qualified] = {
                value: values[value] for value in boundaries
            }

        return {
            "near_duplicate_time_entries": {
                "expected": validator.expected_duplicate_count(),
                "observed": duplicate_count,
            },
            "open_ended_projects": {
                "expected": validator.expected_null_count("projects"),
                "observed": sum(row["end_date"] == "" for row in projects),
            },
            "open_ended_tasks": {
                "expected": validator.expected_null_count("tasks"),
                "observed": sum(row["due_date"] == "" for row in tasks),
            },
            "task_estimate_outliers": {
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


__all__ = ["ProjectManagementManifestGenerator"]
