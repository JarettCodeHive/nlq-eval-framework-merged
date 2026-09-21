"""Finance release manifest generation."""

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
from generators.finance.config import load_finance_config
from generators.finance.config import settings_for_profile
from generators.finance.data_dictionary import FinanceDataDictionaryGenerator
from generators.finance.hashes import FinanceHashComputer
from generators.finance.validators.config import validate_finance_config
from generators.finance.validators.fk_integrity import ACCOUNTING_CHECKS
from generators.finance.validators.fk_integrity import FinanceDuckDBFKValidator
from generators.finance.validators.fk_integrity import MANUAL_FK_CHECKS
from generators.finance.validators.imperfection_rates import (
    FinanceImperfectionRateValidator,
)
from generators.finance.validators.join_paths import FinanceJoinPathValidator
from generators.finance.validators.row_caps import FinanceRowCapValidator


class FinanceManifestGenerator:
    """Build and atomically seal a validated Finance release manifest."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "finance":
            raise ValueError("FinanceManifestGenerator only supports finance")
        validate_finance_config()
        self.generator = generator
        self.settings = generator.settings
        self.finance_config = load_finance_config()

    @classmethod
    def for_profile(cls, profile: str) -> "FinanceManifestGenerator":
        """Create a Finance manifest generator from validated profile config."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def generate_manifest(self) -> dict[str, Any]:
        """Build manifest content only after every available release gate passes."""

        if not self.settings.is_release_profile:
            raise ValueError(
                "Finance manifest generation requires the full profile; "
                f"got profile={self.settings.profile}"
            )

        hashes = FinanceHashComputer(self.generator).compute_exported_csv_hashes()
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
                "Cannot generate Finance manifest because validation failed: "
                + ", ".join(failed_checks[:10])
            )

        observations = self._release_observations()
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
            "tables": table_metadata,
            "hash_algorithm": "sha256",
            "hashes": {
                table_name: table_metadata[table_name]["sha256"]
                for table_name in self.settings.table_order
            },
            "release_artifacts": release_artifacts,
            "distributions": dict(self.settings.distributions),
            "distribution_configuration": self._distribution_configuration(),
            "imperfections": dict(self.settings.imperfections),
            "imperfection_observations": observations["imperfections"],
            "domain_values": dict(self.finance_config["domain_values"]),
            "generation_rules": dict(self.finance_config["generation_rules"]),
            "business_mappings": dict(self.finance_config["business_mappings"]),
            "decimal_policy": dict(self.finance_config["decimal_policy"]),
            "currency_contract": self._currency_contract(),
            "fx_summary": observations["fx"],
            "accounting_rules": list(self.finance_config["accounting_rules"]),
            "conversion_rules": list(self.finance_config["conversion_rules"]),
            "distribution_targets": dict(self.finance_config["distribution_targets"]),
            "imperfection_targets": dict(self.finance_config["imperfection_targets"]),
            "relationships": list(self.finance_config["relationships"]),
            "join_path_requirements": list(
                self.finance_config["join_path_requirements"]
            ),
            "consistency_rules": list(self.finance_config["consistency_rules"]),
            "library_versions": _library_versions(),
            "validation_status": validation_status,
        }

    def write_manifest(self) -> Path:
        """Write manifest.json once, making the Finance release immutable."""

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
            table_config = self.finance_config["tables"][table_name]
            expected_columns = [field["name"] for field in table_config["fields"]]
            if csv_metadata["columns"] != expected_columns:
                raise ValueError(
                    f"{table_name}.csv header differs from the configured schema: "
                    f"expected={expected_columns}, actual={csv_metadata['columns']}"
                )
            metadata_by_table[table_name] = {
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
        return metadata_by_table

    def _release_artifact_metadata(self) -> dict[str, dict[str, Any]]:
        release_schema = self.settings.output_path / "schema.sql"
        dictionary = self.settings.output_path / "data_dictionary.md"
        canonical_ddl = self.settings.schema_source
        erd = canonical_ddl.with_name("finance_er.dbml")
        header_spec = canonical_ddl.with_name("finance_csv_header_spec.md")
        required = (release_schema, dictionary, canonical_ddl, erd, header_spec)
        missing = [path for path in required if not path.is_file()]
        if missing:
            raise FileNotFoundError(
                "Finance release support artifacts are missing. Generate schema SQL "
                "and the data dictionary before the manifest. Missing: "
                + ", ".join(str(path) for path in missing)
            )
        if release_schema.read_bytes() != canonical_ddl.read_bytes():
            raise ValueError("Release schema.sql differs from canonical Finance DDL")
        expected_dictionary = (
            FinanceDataDictionaryGenerator(self.generator)
            .generate_markdown()
            .encode("utf-8")
        )
        if dictionary.read_bytes() != expected_dictionary:
            raise ValueError(
                "Release data_dictionary.md differs from current Finance config"
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
        """Return the configured release path independent of staging location."""

        configured = settings_for_profile(self.settings.profile).output_path
        path = configured / file_name if file_name else configured
        return _relative(path)

    def _distribution_configuration(self) -> dict[str, Any]:
        fx_target = self.finance_config["distribution_targets"]["fx_daily_movement"]
        return {
            "shared_defaults_source": (
                "config/generation/base.json:distribution_defaults"
            ),
            "domain_configuration_source": (
                "config/generation/finance/generation.json:distributions"
            ),
            "settings": build_distribution_metadata(
                self.finance_config["distributions"],
                self.settings.distributions,
                self.finance_config["distribution_targets"],
            ),
            "custom_algorithms": {
                "fx_daily_movement": {
                    "algorithm": fx_target["distribution"],
                    "parameters": dict(
                        self.finance_config["generation_rules"]["fx_daily_movement"]
                    ),
                    "target": dict(fx_target),
                }
            },
        }

    def _currency_contract(self) -> dict[str, Any]:
        mappings = self.finance_config["business_mappings"]
        calendar = self.finance_config["generation_rules"]["fx_calendar"]
        return {
            "reporting_currency": mappings["reporting_currency"],
            "supported_source_currencies": list(
                self.finance_config["domain_values"]["source_currencies"]
            ),
            "supported_pairs": list(calendar["currency_pairs"]),
            "conversion_formula": mappings["fx_conversion"]["formula"],
            "join_fields": list(mappings["fx_conversion"]["join_fields"]),
            "missing_rate_behavior": mappings["fx_conversion"]["null_rate_result"],
            "rounding_order": mappings["fx_conversion"]["rounding"],
        }

    def _validation_status(self) -> dict[str, Any]:
        # Regeneration-based gates must not share consumed RNG/Faker streams with
        # the exporter or with one another. Fresh contexts also make gate order
        # irrelevant to the validation outcome.
        row_results = FinanceRowCapValidator(
            self._fresh_generator()
        ).validate_exported_csvs()
        fk_results = FinanceDuckDBFKValidator(
            self._fresh_generator()
        ).validate_exported_csvs()
        join_results = FinanceJoinPathValidator(
            self._fresh_generator()
        ).validate_exported_csvs()
        imperfection_results = FinanceImperfectionRateValidator(
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
                "accounting_integrity": _named_checks_passed(
                    fk_results, set(ACCOUNTING_CHECKS)
                ),
                "join_paths": _all_passed(join_results),
                "fx_lookup": _prefixed_checks_passed(
                    join_results,
                    ("finance_jp_006", "finance_jp_007", "finance_jp_008"),
                ),
                "imperfection_rates": _all_passed(imperfection_results),
                "reproducibility": {
                    "status": "pending_step_24",
                    "passed": None,
                    "message": (
                        "Clean-room byte comparison is performed by the separate "
                        "Finance reproducibility validator."
                    ),
                },
            },
        }

    def _fresh_generator(self) -> DeterministicGenerator:
        """Return an unconsumed deterministic context for one validation gate."""

        return DeterministicGenerator(self.settings)

    def _release_observations(self) -> dict[str, Any]:
        output = self.settings.output_path
        fx_rows = _read_csv_rows(output / "fx_rates.csv")
        transaction_rows = _read_csv_rows(output / "transactions.csv")
        budget_rows = _read_csv_rows(output / "budgets.csv")

        fx_keys = [
            (row["from_currency"], row["to_currency"], row["rate_date"])
            for row in fx_rows
        ]
        fx_key_set = set(fx_keys)
        null_fx_keys = {
            key for key, row in zip(fx_keys, fx_rows, strict=True) if row["rate"] == ""
        }
        transaction_keys = [
            (
                row["source_currency"],
                row["target_currency"],
                row["transaction_date"],
            )
            for row in transaction_rows
        ]
        identity_rows = [
            row
            for row in fx_rows
            if row["from_currency"] == "USD" and row["to_currency"] == "USD"
        ]

        imperfection_validator = FinanceImperfectionRateValidator(
            self._fresh_generator()
        )
        budget_target = self.finance_config["imperfection_targets"][
            "near_duplicate_budgets"
        ]
        business_keys = budget_target["business_key_fields"]
        budget_counts = Counter(
            tuple(row[field] for field in business_keys) for row in budget_rows
        )
        duplicate_budgets = sum(count - 1 for count in budget_counts.values())
        outlier_minimum = Decimal(
            str(
                self.finance_config["imperfection_targets"][
                    "transaction_amount_outliers"
                ]["minimum_value"]
            )
        )
        observed_outliers = sum(
            Decimal(row["total_amount"]) >= outlier_minimum
            for row in transaction_rows
        )
        boundaries = list(self.settings.imperfections["boundary_dates"])
        boundary_counts = Counter(row["transaction_date"] for row in transaction_rows)

        return {
            "fx": {
                "rows": len(fx_rows),
                "unique_composite_keys": len(fx_key_set),
                "duplicate_composite_keys": len(fx_keys) - len(fx_key_set),
                "null_rates": len(null_fx_keys),
                "populated_rates": len(fx_rows) - len(null_fx_keys),
                "identity_pair_rows": len(identity_rows),
                "identity_pair_null_rates": sum(
                    row["rate"] == "" for row in identity_rows
                ),
                "transaction_rows": len(transaction_rows),
                "transaction_rows_with_lookup": sum(
                    key in fx_key_set for key in transaction_keys
                ),
                "transaction_rows_without_lookup": sum(
                    key not in fx_key_set for key in transaction_keys
                ),
                "transaction_rows_with_null_rate": sum(
                    key in null_fx_keys for key in transaction_keys
                ),
            },
            "imperfections": {
                "missing_fx_rates": {
                    "expected": imperfection_validator.expected_null_count(),
                    "observed": len(null_fx_keys),
                },
                "near_duplicate_budgets": {
                    "expected": imperfection_validator.expected_duplicate_count(),
                    "observed": duplicate_budgets,
                },
                "transaction_amount_outliers": {
                    "expected": imperfection_validator.expected_outlier_count(),
                    "observed": observed_outliers,
                },
                "transaction_boundary_dates": {
                    "expected": boundaries,
                    "observed_counts": {
                        value: boundary_counts[value] for value in boundaries
                    },
                },
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


def _prefixed_checks_passed(
    results: list[IntegrityCheckResult],
    prefixes: tuple[str, ...],
) -> bool:
    matching = [
        result for result in results if result.check_name.startswith(prefixes)
    ]
    return bool(matching) and _all_passed(matching)


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


__all__ = ["FinanceManifestGenerator"]
