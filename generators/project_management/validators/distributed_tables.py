"""Distribution-specific validation for Project Management tables."""

from __future__ import annotations

from datetime import date
from datetime import timedelta
from decimal import Decimal
from decimal import InvalidOperation
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.schema_contract import primary_key_fields
from generators.project_management.config import load_project_management_config
from generators.project_management.validators.generated_tables import (
    validate_project_management_generated_tables,
)


class ProjectManagementDistributedTablesValidator:
    """Validate PM distributions without duplicating relational guards.

    The clean-table validator first proves schema, row counts, keys,
    relationships, assignment membership, chronology, status/date semantics,
    decimals, and clean-stage constraints. This validator then checks Pareto
    bounds and shape, exact non-uniform Poisson assignment frequencies,
    observable Gaussian-mixture clustering, and optional source preservation.
    """

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "project_management":
            raise ValueError(
                "ProjectManagementDistributedTablesValidator only supports "
                "the project_management domain"
            )
        self.generator = generator
        self.settings = generator.settings
        self.config = load_project_management_config()
        self.rules = self.config["generation_rules"]

    def validate(
        self,
        tables: dict[str, Any],
        source_tables: dict[str, Any] | None = None,
    ) -> None:
        """Validate distributed tables and optionally compare their source."""

        validate_project_management_generated_tables(self.generator, tables)
        self._validate_project_budget_distribution(tables)
        self._validate_time_entry_frequency(tables)
        self._validate_date_clustering(tables)
        if source_tables is not None:
            self._validate_source_preservation(tables, source_tables)

    def _validate_project_budget_distribution(
        self,
        tables: dict[str, Any],
    ) -> None:
        """Require bounded, fixed-scale amounts with an observable upper tail."""

        target = self.config["distribution_targets"]["project_budget"]
        spec = self.settings.distributions[target["settings_key"]]
        minimum = Decimal(str(spec["min_amount"]))
        maximum = Decimal(str(spec["max_amount"]))
        scale = int(spec["scale"])
        values = [
            _parse_fixed_decimal(value, scale, "projects.budget_amount")
            for value in tables[target["table"]][target["field"]]
            if not _is_blank(value)
        ]
        if not values:
            raise ValueError("Distributed project budgets contain no populated values")
        if any(value < minimum or value > maximum for value in values):
            raise ValueError(
                "projects.budget_amount is outside configured distribution bounds"
            )

        if len(values) >= 5:
            ordered = sorted(values)
            median = ordered[len(ordered) // 2]
            lower_quarter_limit = minimum + ((maximum - minimum) / Decimal(4))
            if median >= lower_quarter_limit or ordered[-1] <= median * Decimal(2):
                raise ValueError(
                    "projects.budget_amount does not show an observable Pareto tail"
                )

    def _validate_time_entry_frequency(self, tables: dict[str, Any]) -> None:
        """Require exact row reconciliation and non-uniform assignment counts."""

        entries = tables["time_entries"]
        expected_rows = self.generator.row_count("time_entries")
        if len(entries) != expected_rows:
            raise ValueError(
                "Distributed time-entry count differs from configured row target"
            )
        frequencies = entries.groupby(["task_id", "resource_id"]).size()
        assignments = tables["task_resources"]
        declared = set(
            assignments[["task_id", "resource_id"]].itertuples(
                index=False,
                name=None,
            )
        )
        if set(frequencies.index) != declared:
            raise ValueError(
                "Distributed time-entry frequencies do not cover every assignment"
            )
        if int(frequencies.sum()) != expected_rows:
            raise ValueError(
                "Time-entry frequencies do not reconcile to configured row target"
            )
        if len(frequencies) > 1 and int(frequencies.nunique()) == 1:
            raise ValueError("Time-entry frequencies do not vary between assignments")

    def _validate_date_clustering(self, tables: dict[str, Any]) -> None:
        """Require project starts to exhibit the configured mixture centers."""

        spec = self.settings.distributions["date_clustering"]
        start = date.fromisoformat(self.rules["date_windows"]["project_activity_start"])
        horizon = date.fromisoformat(
            self.rules["date_windows"]["project_planning_horizon_end"]
        )
        latest_start = horizon - timedelta(
            days=int(self.rules["projects"]["duration_days"]["minimum"])
        )
        span = (latest_start - start).days
        if span <= 0:
            raise ValueError("Configured PM project distribution window is invalid")

        normalized = [
            (date.fromisoformat(str(value)) - start).days / span
            for value in tables["projects"]["start_date"]
        ]
        tolerance = 3 * float(spec["std_fraction"])
        centers = [float(value) for value in spec["component_centers"]]
        clustered = sum(
            min(abs(value - center) for center in centers) <= tolerance
            for value in normalized
        )
        if normalized and clustered / len(normalized) < 0.8:
            raise ValueError(
                "projects.start_date does not show configured Gaussian clustering"
            )

    def _validate_source_preservation(
        self,
        tables: dict[str, Any],
        source_tables: dict[str, Any],
    ) -> None:
        """Reject row, key, clean-NULL, or undeclared field drift."""

        if tuple(source_tables) != self.settings.table_order:
            raise ValueError(
                "Source Project Management table order differs from config"
            )
        mutable = _distribution_mutable_fields(self.config)
        for table_name in self.settings.table_order:
            source = source_tables[table_name]
            distributed = tables[table_name]
            if source.columns.tolist() != distributed.columns.tolist():
                raise ValueError(
                    f"{table_name} columns changed during distribution application"
                )
            if len(source) != len(distributed):
                raise ValueError(
                    f"{table_name} row count changed during distribution application"
                )

            for key in primary_key_fields(self.config["tables"][table_name]):
                if not source[key].equals(distributed[key]):
                    raise ValueError(
                        f"{table_name}.{key} changed during distribution application"
                    )

            for column_name in source.columns:
                if column_name not in mutable[table_name] and not source[
                    column_name
                ].equals(distributed[column_name]):
                    raise ValueError(
                        f"{table_name}.{column_name} changed unexpectedly during "
                        "distribution application"
                    )

        budget = self.config["distribution_targets"]["project_budget"]
        source_nulls = source_tables[budget["table"]][budget["field"]].map(_is_blank)
        distributed_nulls = tables[budget["table"]][budget["field"]].map(_is_blank)
        if not source_nulls.equals(distributed_nulls):
            raise ValueError(
                "projects.budget_amount business NULL positions changed during "
                "distribution application"
            )


def validate_project_management_distributed_tables(
    generator: DeterministicGenerator,
    tables: dict[str, Any],
    source_tables: dict[str, Any] | None = None,
) -> None:
    """Run every immediate distributed PM guard."""

    ProjectManagementDistributedTablesValidator(generator).validate(
        tables,
        source_tables,
    )


def _distribution_mutable_fields(
    config: dict[str, Any],
) -> dict[str, set[str]]:
    """Derive direct distribution targets and chronology dependencies."""

    mutable = {table_name: set() for table_name in config["tables"]}
    date_target_tables: set[str] = set()
    for target in config["distribution_targets"].values():
        table_name = target.get("table")
        field_name = target.get("field")
        if table_name in mutable and field_name is not None:
            mutable[table_name].add(field_name)
        if table_name in mutable:
            mutable[table_name].update(target.get("group_by_fields", []))
        for dotted_target in target.get("targets", []):
            dotted_table, dotted_field = dotted_target.split(".", 1)
            mutable[dotted_table].add(dotted_field)
            date_target_tables.add(dotted_table)

    for table_name in date_target_tables:
        if any(
            field["name"] == "created_at"
            for field in config["tables"][table_name]["fields"]
        ):
            mutable[table_name].add("created_at")
    return mutable


def _parse_fixed_decimal(value: Any, scale: int, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} contains a non-decimal value") from exc
    if not parsed.is_finite() or parsed.as_tuple().exponent != -scale:
        raise ValueError(f"{label} does not use scale {scale}")
    return parsed


def _is_blank(value: Any) -> bool:
    return value is None or value == "" or value != value


__all__ = [
    "ProjectManagementDistributedTablesValidator",
    "validate_project_management_distributed_tables",
]
