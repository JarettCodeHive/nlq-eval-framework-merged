"""Immediate validation for controlled Project Management imperfections."""

from __future__ import annotations

from decimal import Decimal
from decimal import InvalidOperation
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.imperfections import count_from_pct
from generators.core.schema_contract import primary_key_fields
from generators.project_management.config import load_project_management_config
from generators.project_management.validators.distributed_tables import (
    validate_project_management_distributed_tables,
)


class ProjectManagementImperfectTablesValidator:
    """Validate exact PM defect results and reject undeclared mutations.

    This immediate gate measures the four configured imperfection classes and
    compares base rows with the distributed source. Step 14 remains responsible
    for the complete post-imperfection relational and temporal audit.
    """

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "project_management":
            raise ValueError(
                "ProjectManagementImperfectTablesValidator only supports the "
                "project_management domain"
            )
        self.generator = generator
        self.settings = generator.settings
        self.config = self.settings.imperfections
        self.pm_config = load_project_management_config()
        self.targets = self.pm_config["imperfection_targets"]

    def validate(
        self,
        tables: dict[str, Any],
        source_tables: dict[str, Any] | None = None,
    ) -> None:
        """Validate structure, exact target rates, and optional source drift."""

        if source_tables is not None:
            validate_project_management_distributed_tables(
                self.generator,
                source_tables,
            )
        self._validate_table_contracts(tables)
        self._validate_primary_keys(tables)
        self._validate_near_duplicate_time_entries(tables)
        self._validate_open_ended_ranges(tables)
        self._validate_task_estimate_outliers(tables)
        self._validate_boundary_dates(tables)
        if source_tables is not None:
            self._validate_source_preservation(tables, source_tables)

    def _validate_table_contracts(self, tables: dict[str, Any]) -> None:
        """Require signed columns and only the configured duplicate row growth."""

        if tuple(tables) != self.settings.table_order:
            raise ValueError("Imperfect Project Management table order differs")
        duplicate_count = self._expected_duplicate_count()
        for table_name in self.settings.table_order:
            table = tables[table_name]
            expected_columns = [
                field["name"]
                for field in self.pm_config["tables"][table_name]["fields"]
            ]
            if table.columns.tolist() != expected_columns:
                raise ValueError(f"{table_name} columns changed after PM imperfections")
            expected_rows = self.generator.row_count(table_name)
            if table_name == "time_entries":
                expected_rows += duplicate_count
            if len(table) != expected_rows:
                raise ValueError(
                    f"{table_name} imperfect row count differs from expected: "
                    f"expected {expected_rows}, got {len(table)}"
                )
            if len(table) > self.settings.max_rows_per_table:
                raise ValueError(f"{table_name} exceeds the configured row cap")

    def _validate_primary_keys(self, tables: dict[str, Any]) -> None:
        """Require populated unique scalar and composite primary keys."""

        for table_name in self.settings.table_order:
            keys = primary_key_fields(self.pm_config["tables"][table_name])
            table = tables[table_name]
            if any(_blank_count(table[field]) for field in keys):
                raise ValueError(f"{table_name} contains blank primary keys")
            if table.duplicated(keys).any():
                raise ValueError(f"{table_name} contains duplicate primary keys")

    def _validate_near_duplicate_time_entries(
        self,
        tables: dict[str, Any],
    ) -> None:
        """Validate duplicate count, fresh IDs, keys, and variation fields."""

        entries = tables["time_entries"]
        base_count = self.generator.row_count("time_entries")
        expected = self._expected_duplicate_count()
        base_rows = entries.iloc[:base_count]
        duplicate_rows = entries.iloc[base_count:]
        target = self.targets["near_duplicate_time_entries"]
        business_keys = target["business_key_fields"]
        variation_fields = set(target["variation_fields"])

        if len(duplicate_rows) != expected:
            raise ValueError("Near-duplicate time-entry count differs from config")
        expected_ids = list(
            range(
                int(base_rows["entry_id"].max()) + 1,
                int(base_rows["entry_id"].max()) + expected + 1,
            )
        )
        if duplicate_rows["entry_id"].tolist() != expected_ids:
            raise ValueError(
                "Near-duplicate time-entry IDs are not fresh and sequential"
            )
        if base_rows.duplicated(business_keys).any():
            raise ValueError("Base time-entry business keys are not unique")

        source_by_key = base_rows.set_index(business_keys, drop=False)
        unchanged = set(entries.columns) - variation_fields - {"entry_id"}
        for row in duplicate_rows.itertuples(index=False):
            key = tuple(getattr(row, field) for field in business_keys)
            lookup: Any = key[0] if len(key) == 1 else key
            if lookup not in source_by_key.index:
                raise ValueError("Near-duplicate time entry has no source business key")
            source = source_by_key.loc[lookup]
            if any(getattr(row, field) != source[field] for field in unchanged):
                raise ValueError(
                    "Near-duplicate time entry changed a non-variation field"
                )
            if not any(
                getattr(row, field) != source[field] for field in variation_fields
            ):
                raise ValueError("Near-duplicate time entry is byte-identical")
            _require_decimal_scale(row.hours, 2, "time_entries.hours")

        actual = int(entries.duplicated(business_keys).sum())
        if actual != expected:
            raise ValueError(
                "Time-entry business-key duplicate count differs from config: "
                f"expected {expected}, got {actual}"
            )

    def _validate_open_ended_ranges(self, tables: dict[str, Any]) -> None:
        """Measure project and task NULL targets independently by eligibility."""

        for target_name in ("open_ended_projects", "open_ended_tasks"):
            target = self.targets[target_name]
            table = tables[target["table"]]
            missing = table[table[target["field"]].map(_is_blank)]
            expected = count_from_pct(
                self.generator.row_count(target["table"]),
                float(self.config["null_pct"]),
            )
            if len(missing) != expected:
                raise ValueError(
                    f"{target['table']}.{target['field']} NULL count differs from "
                    f"configured rate: expected {expected}, got {len(missing)}"
                )
            if not set(missing["status"]).issubset(set(target["eligible_statuses"])):
                raise ValueError(f"{target_name} contains an ineligible status")

    def _validate_task_estimate_outliers(self, tables: dict[str, Any]) -> None:
        """Require the exact fixed-scale outlier count and configured range."""

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
        expected = count_from_pct(
            self.generator.row_count(target["table"]),
            float(self.config["outlier_pct"]),
        )
        outliers: list[Decimal] = []
        for value in tables[target["table"]][target["field"]]:
            amount = _require_decimal_scale(
                value,
                int(target["scale"]),
                f"{target['table']}.{target['field']}",
            )
            if amount >= minimum:
                if amount > maximum:
                    raise ValueError("A task-estimate outlier exceeds its maximum")
                outliers.append(amount)
            elif amount > clean_maximum:
                raise ValueError(
                    "A task estimate falls between the clean and outlier ranges"
                )
        if len(outliers) != expected:
            raise ValueError(
                "Task-estimate outlier count differs from configured rate: "
                f"expected {expected}, got {len(outliers)}"
            )

    def _validate_boundary_dates(self, tables: dict[str, Any]) -> None:
        """Require every configured value in every declared primary target."""

        boundaries = set(self.config["boundary_dates"])
        target = self.targets["coordinated_boundary_dates"]
        for qualified in target["targets"]:
            table_name, field_name = qualified.split(".", 1)
            actual = {
                str(value).split("T", 1)[0]
                for value in tables[table_name][field_name]
                if not _is_blank(value)
            }
            missing = boundaries - actual
            if missing:
                raise ValueError(
                    f"{qualified} is missing configured boundary dates: "
                    f"{sorted(missing)}"
                )

    def _validate_source_preservation(
        self,
        tables: dict[str, Any],
        source_tables: dict[str, Any],
    ) -> None:
        """Allow changes only in config-declared imperfection fields."""

        if tuple(source_tables) != self.settings.table_order:
            raise ValueError("Source Project Management table order differs")
        mutable = _imperfection_mutable_fields(self.pm_config)
        for table_name in self.settings.table_order:
            source = source_tables[table_name].reset_index(drop=True)
            result = tables[table_name].iloc[: len(source)].reset_index(drop=True)
            if source.columns.tolist() != result.columns.tolist():
                raise ValueError(
                    f"{table_name} columns changed during imperfection injection"
                )
            for column_name in source.columns:
                if column_name not in mutable[table_name] and not source[
                    column_name
                ].equals(result[column_name]):
                    raise ValueError(
                        f"{table_name}.{column_name} changed unexpectedly during "
                        "imperfection injection"
                    )

    def _expected_duplicate_count(self) -> int:
        return count_from_pct(
            self.generator.row_count("time_entries"),
            float(self.config["duplicate_pct"]),
        )


def validate_project_management_imperfect_tables(
    generator: DeterministicGenerator,
    tables: dict[str, Any],
    source_tables: dict[str, Any] | None = None,
) -> None:
    """Run every immediate PM imperfection-result guard."""

    ProjectManagementImperfectTablesValidator(generator).validate(
        tables,
        source_tables,
    )


def _imperfection_mutable_fields(
    config: dict[str, Any],
) -> dict[str, set[str]]:
    """Derive the allowed base-row mutations from PM target configuration."""

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


def _require_decimal_scale(value: Any, scale: int, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} contains a non-decimal value") from exc
    if not parsed.is_finite() or parsed.as_tuple().exponent != -scale:
        raise ValueError(f"{label} does not use scale {scale}")
    return parsed


def _blank_count(series: Any) -> int:
    return int(series.isna().sum() + series.astype(str).eq("").sum())


def _is_blank(value: Any) -> bool:
    return value is None or value == "" or value != value


__all__ = [
    "ProjectManagementImperfectTablesValidator",
    "validate_project_management_imperfect_tables",
]
