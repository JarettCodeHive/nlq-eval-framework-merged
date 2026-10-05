"""Project Management imperfection-rate validation for final datasets."""

from __future__ import annotations

from decimal import Decimal
from decimal import InvalidOperation
from pathlib import Path
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.imperfections import count_from_pct
from generators.core.integrity import IntegrityCheckResult
from generators.core.integrity import assert_all_passed
from generators.core.integrity import failed
from generators.core.integrity import passed
from generators.project_management.config import load_project_management_config
from generators.project_management.config import settings_for_profile
from generators.project_management.distributions import (
    ProjectManagementDistributionApplier,
)
from generators.project_management.imperfections import (
    ProjectManagementImperfectionInjector,
)
from generators.project_management.validators.config import (
    validate_project_management_config,
)
from generators.project_management.validators.relational import (
    ProjectManagementRelationalValidator,
)


class ProjectManagementImperfectionRateValidator:
    """Validate observable PM imperfections against deterministic config."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "project_management":
            raise ValueError(
                "ProjectManagementImperfectionRateValidator only supports "
                "project_management"
            )
        validate_project_management_config()
        self.generator = generator
        self.settings = generator.settings
        self.config = self.settings.imperfections
        self.pm_config = load_project_management_config()
        self.targets = self.pm_config["imperfection_targets"]

    @classmethod
    def for_profile(
        cls,
        profile: str,
    ) -> "ProjectManagementImperfectionRateValidator":
        """Create a PM imperfection validator from profile configuration."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def validate_exported_csvs(self) -> list[IntegrityCheckResult]:
        """Validate configured final CSVs against a clean distributed stage."""

        return self.validate_csv_directory(self.settings.output_path)

    def validate_csv_directory(
        self,
        directory: Path,
    ) -> list[IntegrityCheckResult]:
        """Validate one persisted directory against fresh deterministic data."""

        tables = self._read_csv_tables(self._csv_paths(directory))
        clean_generator = DeterministicGenerator(self.settings)
        source = ProjectManagementDistributionApplier(
            clean_generator
        ).generate_distributed_tables()
        return self.validate_tables(tables, source_tables=source)

    def generate_and_validate(self) -> list[IntegrityCheckResult]:
        """Generate distributed and imperfect PM stages and validate rates."""

        source = ProjectManagementDistributionApplier(
            self.generator
        ).generate_distributed_tables()
        tables = ProjectManagementImperfectionInjector(self.generator).apply_to_tables(
            source
        )
        return self.validate_tables(tables, source_tables=source)

    def validate_tables(
        self,
        tables: dict[str, Any],
        source_tables: dict[str, Any] | None = None,
    ) -> list[IntegrityCheckResult]:
        """Return independent results for every PM imperfection contract."""

        if source_tables is None:
            source_tables = ProjectManagementDistributionApplier(
                DeterministicGenerator(self.settings)
            ).generate_distributed_tables()
        results: list[IntegrityCheckResult] = []
        results.extend(self._validate_duplicate_time_entries(tables))
        results.extend(self._validate_open_ended_ranges(tables))
        results.extend(self._validate_task_estimate_outliers(tables))
        results.extend(self._validate_boundary_dates(tables))
        results.extend(self._validate_scope(tables, source_tables))
        results.append(self._validate_relational_invariants(tables))
        return results

    def validate_exported_or_raise(self) -> list[IntegrityCheckResult]:
        """Validate exported PM rates and raise a combined error on failure."""

        results = self.validate_exported_csvs()
        assert_all_passed(results)
        return results

    def expected_duplicate_count(self) -> int:
        """Return expected near-duplicate time-entry rows."""

        return count_from_pct(
            self.generator.row_count("time_entries"),
            float(self.config["duplicate_pct"]),
        )

    def expected_null_count(self, table_name: str) -> int:
        """Return expected controlled NULL rows for a PM table."""

        return count_from_pct(
            self.generator.row_count(table_name),
            float(self.config["null_pct"]),
        )

    def expected_outlier_count(self) -> int:
        """Return expected task-estimate outlier rows."""

        return count_from_pct(
            self.generator.row_count("tasks"),
            float(self.config["outlier_pct"]),
        )

    def _validate_duplicate_time_entries(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        entries = tables["time_entries"]
        target = self.targets["near_duplicate_time_entries"]
        keys = target["business_key_fields"]
        variations = set(target["variation_fields"])
        duplicate_count = 0
        valid_key_count = 0
        valid_variation_count = 0
        for _, group in entries.groupby(keys, dropna=False, sort=False):
            if len(group) == 1:
                continue
            additional = len(group) - 1
            duplicate_count += additional
            if len(group) == 2:
                valid_key_count += additional
            if _valid_duplicate_group(group, set(keys), variations):
                valid_variation_count += additional
        expected = self.expected_duplicate_count()
        return [
            _exact_count_result(
                "time_entries.near_duplicate_rate", duplicate_count, expected
            ),
            _exact_count_result(
                "time_entries.near_duplicate_business_keys",
                valid_key_count,
                expected,
            ),
            _exact_count_result(
                "time_entries.near_duplicate_variations",
                valid_variation_count,
                expected,
            ),
        ]

    def _validate_open_ended_ranges(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        results: list[IntegrityCheckResult] = []
        for target_name in ("open_ended_projects", "open_ended_tasks"):
            target = self.targets[target_name]
            table = tables[target["table"]]
            missing = table[table[target["field"]].map(_is_blank)]
            expected = self.expected_null_count(target["table"])
            results.append(
                _exact_count_result(
                    f"{target['table']}.{target['field']}.null_rate",
                    len(missing),
                    expected,
                )
            )
            invalid = int((~missing["status"].isin(target["eligible_statuses"])).sum())
            results.append(
                _zero_count_result(
                    f"{target['table']}.{target['field']}.eligible_status",
                    invalid,
                    "NULL row(s) have ineligible status",
                )
            )
        return results

    def _validate_task_estimate_outliers(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        target = self.targets["task_estimate_outliers"]
        minimum = Decimal(str(target["minimum_value"]))
        maximum = Decimal(str(target["maximum_value"]))
        clean_maximum = Decimal(
            str(
                self.pm_config["generation_rules"]["tasks"]["estimate_hours"][
                    "maximum_amount"
                ]
            )
        )
        scale = int(target["scale"])
        outliers: list[Decimal] = []
        invalid = 0
        gap_values = 0
        for value in tables["tasks"]["estimate_hours"]:
            if _is_blank(value):
                continue
            try:
                amount = Decimal(str(value))
            except InvalidOperation:
                invalid += 1
                continue
            if amount >= minimum:
                outliers.append(amount)
            elif amount > clean_maximum:
                gap_values += 1
        return [
            _exact_count_result(
                "tasks.estimate_hours.outlier_rate",
                len(outliers),
                self.expected_outlier_count(),
            ),
            _condition_result(
                "tasks.estimate_hours.outlier_range",
                invalid == 0
                and gap_values == 0
                and all(minimum <= value <= maximum for value in outliers),
                f"all outliers are between {minimum} and {maximum}",
                "invalid decimal, gap value, or outlier outside configured range",
            ),
            _condition_result(
                "tasks.estimate_hours.outlier_scale",
                invalid == 0
                and all(value.as_tuple().exponent == -scale for value in outliers),
                f"all outliers use scale {scale}",
                f"outliers must use scale {scale}",
            ),
        ]

    def _validate_boundary_dates(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        boundaries = set(self.config["boundary_dates"])
        results: list[IntegrityCheckResult] = []
        for qualified in self.targets["coordinated_boundary_dates"]["targets"]:
            table_name, field_name = qualified.split(".", 1)
            actual = {
                str(value).split("T", 1)[0]
                for value in tables[table_name][field_name]
                if not _is_blank(value)
            }
            missing = sorted(boundaries - actual)
            results.append(
                _condition_result(
                    f"{qualified}.boundary_values",
                    not missing,
                    f"all {len(boundaries)} boundary values are present",
                    f"missing boundary values: {missing}",
                )
            )
        return results

    def _validate_scope(
        self,
        tables: dict[str, Any],
        source_tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        contract_errors = 0
        field_errors = 0
        mutable = _mutable_fields(self.pm_config)
        if (
            tuple(tables) != self.settings.table_order
            or tuple(source_tables) != self.settings.table_order
        ):
            contract_errors += 1
        for table_name in self.settings.table_order:
            if table_name not in tables or table_name not in source_tables:
                contract_errors += 1
                continue
            source = source_tables[table_name].reset_index(drop=True)
            result = tables[table_name].reset_index(drop=True)
            if result.columns.tolist() != source.columns.tolist():
                contract_errors += 1
                continue
            expected_rows = len(source) + (
                self.expected_duplicate_count() if table_name == "time_entries" else 0
            )
            if len(result) != expected_rows:
                contract_errors += 1
                continue
            base_rows = result.iloc[: len(source)].reset_index(drop=True)
            for field_name in source.columns:
                if field_name in mutable[table_name]:
                    continue
                if _normalized_values(source[field_name]) != _normalized_values(
                    base_rows[field_name]
                ):
                    field_errors += 1
        return [
            _zero_count_result(
                "project_management.imperfection_scope.table_contract",
                contract_errors,
                "table contract violation(s)",
            ),
            _zero_count_result(
                "project_management.imperfection_scope.unapproved_fields",
                field_errors,
                "unapproved field modification(s)",
            ),
        ]

    def _validate_relational_invariants(
        self,
        tables: dict[str, Any],
    ) -> IntegrityCheckResult:
        results = ProjectManagementRelationalValidator(self.generator).validate_tables(
            tables
        )
        failures = [result.check_name for result in results if not result.passed]
        return _condition_result(
            "project_management.relational_date_invariants",
            not failures,
            f"all {len(results)} relational and date checks passed",
            f"failed checks: {failures[:10]}",
        )

    def _csv_paths(self, directory: Path) -> dict[str, Path]:
        paths = {
            table_name: directory / f"{table_name}.csv"
            for table_name in self.settings.table_order
        }
        missing = [path for path in paths.values() if not path.exists()]
        if missing:
            raise FileNotFoundError(
                "Project Management CSVs are missing. Export the requested "
                "profile first or use generated validation. Missing: "
                + ", ".join(str(path) for path in missing)
            )
        return paths

    def _read_csv_tables(self, paths: dict[str, Path]) -> dict[str, Any]:
        pd = _require_pandas()
        tables: dict[str, Any] = {}
        for table_name in self.settings.table_order:
            table = pd.read_csv(
                paths[table_name],
                keep_default_na=False,
                dtype=str,
            )
            for field in self.pm_config["tables"][table_name]["fields"]:
                name = field["name"]
                if field["type"] == "integer":
                    table[name] = table[name].map(
                        lambda value: "" if value == "" else int(value)
                    )
                elif field["type"] == "boolean":
                    table[name] = table[name].map(
                        lambda value: str(value).lower() == "true"
                    )
            tables[table_name] = table
        return tables


def _valid_duplicate_group(
    group: Any,
    business_keys: set[str],
    variation_fields: set[str],
) -> bool:
    if len(group) != 2:
        return False
    ignored = business_keys | variation_fields | {"entry_id"}
    stable = [field for field in group.columns if field not in ignored]
    first, second = group.iloc[0], group.iloc[1]
    return all(str(first[field]) == str(second[field]) for field in stable) and any(
        str(first[field]) != str(second[field]) for field in variation_fields
    )


def _mutable_fields(config: dict[str, Any]) -> dict[str, set[str]]:
    mutable = {table_name: set() for table_name in config["tables"]}
    targets = config["imperfection_targets"]
    for target_name in (
        "open_ended_projects",
        "open_ended_tasks",
        "task_estimate_outliers",
    ):
        target = targets[target_name]
        mutable[target["table"]].add(target["field"])
    boundary = targets["coordinated_boundary_dates"]
    for qualified in boundary["targets"] + boundary["dependent_updates"]:
        table_name, field_name = qualified.split(".", 1)
        mutable[table_name].add(field_name)
    return mutable


def _normalized_values(series: Any) -> list[str]:
    return [_normalized_value(value) for value in series.tolist()]


def _normalized_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if _is_blank(value):
        return ""
    return str(value)


def _is_blank(value: Any) -> bool:
    return value is None or value == "" or value != value


def _exact_count_result(
    check_name: str,
    actual: int,
    expected: int,
) -> IntegrityCheckResult:
    return (
        passed(check_name, f"{actual} row(s)")
        if actual == expected
        else failed(check_name, f"expected {expected}, got {actual}")
    )


def _zero_count_result(
    check_name: str,
    count: int,
    message: str,
) -> IntegrityCheckResult:
    return (
        passed(check_name, "zero invalid rows")
        if count == 0
        else failed(check_name, f"{count} {message}")
    )


def _condition_result(
    check_name: str,
    condition: bool,
    success: str,
    failure: str,
) -> IntegrityCheckResult:
    return passed(check_name, success) if condition else failed(check_name, failure)


def _require_pandas() -> Any:
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError(
            "Missing required dependency 'pandas'. Create the root .venv and "
            "install requirements.txt before validating PM imperfection rates."
        ) from exc
    return pd


__all__ = ["ProjectManagementImperfectionRateValidator"]
