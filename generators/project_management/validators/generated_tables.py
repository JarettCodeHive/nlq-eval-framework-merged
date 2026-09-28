"""Immediate guards for clean Project Management base tables."""

from __future__ import annotations

import re
from datetime import date
from datetime import datetime
from decimal import Decimal
from decimal import InvalidOperation
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.schema_contract import primary_key_fields
from generators.project_management.config import load_base_config
from generators.project_management.config import load_project_management_config


class ProjectManagementGeneratedTablesValidator:
    """Reject invalid clean PM tables before base generation returns."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "project_management":
            raise ValueError(
                "ProjectManagementGeneratedTablesValidator only supports the "
                "project_management domain"
            )
        self.generator = generator
        self.settings = generator.settings
        self.base_config = load_base_config()
        self.config = load_project_management_config()

    def validate(self, tables: dict[str, Any]) -> None:
        """Validate clean table shape, relationships, and PM invariants."""

        self._validate_table_contracts(tables)
        self._validate_keys_and_required_fields(tables)
        self._validate_foreign_keys(tables)
        self._validate_semantic_assignment_membership(tables)
        self._validate_domain_values(tables)
        self._validate_decimal_fields(tables)
        self._validate_projects_and_tasks(tables)
        self._validate_assignments(tables)
        self._validate_milestones(tables)
        self._validate_time_entries(tables)
        self._validate_required_join_cases(tables)
        self._validate_currency(tables)
        self._validate_clean_stage(tables)

    def _validate_table_contracts(self, tables: dict[str, Any]) -> None:
        if tuple(tables) != self.settings.table_order:
            raise ValueError("Generated PM table order differs from config")
        for table_name in self.settings.table_order:
            expected_columns = [
                field["name"] for field in self.config["tables"][table_name]["fields"]
            ]
            if tables[table_name].columns.tolist() != expected_columns:
                raise ValueError(f"{table_name} generated columns differ from contract")
            expected_rows = self.generator.row_count(table_name)
            actual_rows = len(tables[table_name])
            if actual_rows != expected_rows:
                raise ValueError(
                    f"{table_name} generated row count differs from config: "
                    f"expected {expected_rows}, got {actual_rows}"
                )

    def _validate_keys_and_required_fields(self, tables: dict[str, Any]) -> None:
        for table_name in self.settings.table_order:
            table_config = self.config["tables"][table_name]
            table = tables[table_name]
            key_fields = primary_key_fields(table_config)
            if any(_has_blanks(table[field]) for field in key_fields):
                raise ValueError(f"{table_name} contains blank primary keys")
            if table.duplicated(key_fields).any():
                raise ValueError(f"{table_name} contains duplicate primary keys")
            for field in table_config["fields"]:
                if not field["nullable"] and _has_blanks(table[field["name"]]):
                    raise ValueError(
                        f"{table_name}.{field['name']} contains blank required values"
                    )

    def _validate_foreign_keys(self, tables: dict[str, Any]) -> None:
        for table_name, table_config in self.config["tables"].items():
            for field in table_config["fields"]:
                reference = field.get("references")
                if reference is None:
                    continue
                parent_values = set(
                    tables[reference["table"]][reference["field"]].tolist()
                )
                child_values = {
                    value
                    for value in tables[table_name][field["name"]].tolist()
                    if not _is_blank(value)
                }
                if child_values - parent_values:
                    raise ValueError(
                        f"{table_name}.{field['name']} contains orphan values"
                    )

    def _validate_semantic_assignment_membership(self, tables: dict[str, Any]) -> None:
        mapping = self.config["business_mappings"]["time_entry_assignment_membership"]
        fields = mapping["fields"]
        assignments = {
            tuple(row)
            for row in tables[mapping["source_table"]][fields].itertuples(
                index=False, name=None
            )
        }
        logged = {
            tuple(row)
            for row in tables["time_entries"][fields].itertuples(index=False, name=None)
        }
        if logged - assignments:
            raise ValueError("time_entries contain undeclared task/resource pairs")

    def _validate_domain_values(self, tables: dict[str, Any]) -> None:
        values = self.config["domain_values"]
        memberships = (
            ("projects", "status", "project_statuses"),
            ("projects", "priority", "project_priorities"),
            ("resources", "role", "resource_roles"),
            ("resources", "department", "departments"),
            ("resources", "location", "locations"),
            ("tasks", "status", "task_statuses"),
            ("tasks", "task_type", "task_types"),
            ("task_resources", "assignment_role", "assignment_roles"),
            ("milestones", "milestone_type", "milestone_types"),
            ("milestones", "status", "milestone_statuses"),
            ("time_entries", "work_type", "work_types"),
            ("time_entries", "notes", "time_entry_note_templates"),
        )
        for table_name, field_name, values_name in memberships:
            actual = {
                value
                for value in tables[table_name][field_name].tolist()
                if not _is_blank(value)
            }
            unknown = actual - set(values[values_name])
            if unknown:
                raise ValueError(
                    f"{table_name}.{field_name} contains unknown domain values"
                )

    def _validate_decimal_fields(self, tables: dict[str, Any]) -> None:
        for table_name, table_config in self.config["tables"].items():
            for field in table_config["fields"]:
                if field["type"] != "decimal":
                    continue
                field_name = field["name"]
                for value in tables[table_name][field_name]:
                    if _is_blank(value):
                        continue
                    _validate_fixed_decimal(
                        value,
                        int(field["precision"]),
                        int(field["scale"]),
                        f"{table_name}.{field_name}",
                    )

    def _validate_projects_and_tasks(self, tables: dict[str, Any]) -> None:
        projects = tables["projects"]
        tasks = tables["tasks"]
        project_lookup = projects.set_index("project_id")
        completed_status = self.config["business_mappings"]["task_status_dates"][
            "completed_status"
        ]
        for row in projects.itertuples(index=False):
            created = datetime.fromisoformat(row.created_at).date()
            start = date.fromisoformat(row.start_date)
            end = date.fromisoformat(row.end_date)
            if not created <= start <= end:
                raise ValueError("Project dates are not chronological")

        for row in tasks.itertuples(index=False):
            project = project_lookup.loc[row.project_id]
            project_start = date.fromisoformat(project["start_date"])
            project_end = date.fromisoformat(project["end_date"])
            created = datetime.fromisoformat(row.created_at).date()
            start = date.fromisoformat(row.start_date)
            due = date.fromisoformat(row.due_date)
            if not created <= start <= due:
                raise ValueError("Task dates are not chronological")
            if not project_start <= start <= due <= project_end:
                raise ValueError("Task dates fall outside the parent project")
            if row.status == completed_status:
                if not row.completed_date:
                    raise ValueError("Completed task lacks completed_date")
                completed = date.fromisoformat(row.completed_date)
                if not start <= completed <= project_end:
                    raise ValueError("Task completion falls outside its valid window")
            elif row.completed_date:
                raise ValueError("Non-completed task unexpectedly has completed_date")

    def _validate_assignments(self, tables: dict[str, Any]) -> None:
        tasks = tables["tasks"].set_index("task_id")
        assignments = tables["task_resources"]
        expected_total = Decimal(self.config["decimal_policy"]["allocation_total"])
        for row in assignments.itertuples(index=False):
            task = tasks.loc[row.task_id]
            task_start = date.fromisoformat(task["start_date"])
            task_due = date.fromisoformat(task["due_date"])
            assigned = date.fromisoformat(row.assigned_at)
            created = datetime.fromisoformat(row.created_at).date()
            if not task_start <= assigned <= task_due or created > assigned:
                raise ValueError("Task-resource assignment dates are invalid")
            if row.released_at:
                released = date.fromisoformat(row.released_at)
                if not assigned <= released <= task_due:
                    raise ValueError("Task-resource release date is invalid")

        totals = (
            assignments.assign(_allocation=assignments["allocation_pct"].map(Decimal))
            .groupby("task_id")["_allocation"]
            .sum()
        )
        if not totals.eq(expected_total).all():
            raise ValueError("Task-resource allocations do not sum to 100.00")
        if assignments["allocation_pct"].map(Decimal).le(0).any():
            raise ValueError("Task-resource allocations must be positive")

    def _validate_milestones(self, tables: dict[str, Any]) -> None:
        projects = tables["projects"].set_index("project_id")
        completed_status = self.config["business_mappings"]["milestone_completion"][
            "completed_status"
        ]
        for row in tables["milestones"].itertuples(index=False):
            project = projects.loc[row.project_id]
            project_start = date.fromisoformat(project["start_date"])
            project_end = date.fromisoformat(project["end_date"])
            planned = date.fromisoformat(row.planned_date)
            created = datetime.fromisoformat(row.created_at).date()
            if not project_start <= planned <= project_end or created > planned:
                raise ValueError("Milestone dates fall outside the parent project")
            if row.status == completed_status:
                if not row.actual_date:
                    raise ValueError("Completed milestone lacks actual_date")
                actual = date.fromisoformat(row.actual_date)
                if not project_start <= actual <= project_end:
                    raise ValueError("Milestone actual_date is outside the project")
            elif row.actual_date:
                raise ValueError("Incomplete milestone unexpectedly has actual_date")

    def _validate_time_entries(self, tables: dict[str, Any]) -> None:
        tasks = tables["tasks"].set_index("task_id")
        assignments = {
            (int(row.task_id), int(row.resource_id)): row
            for row in tables["task_resources"].itertuples(index=False)
        }
        for row in tables["time_entries"].itertuples(index=False):
            task = tasks.loc[row.task_id]
            assignment = assignments[(row.task_id, row.resource_id)]
            entry = date.fromisoformat(row.entry_date)
            assigned = date.fromisoformat(assignment.assigned_at)
            released = (
                date.fromisoformat(assignment.released_at)
                if assignment.released_at
                else date.fromisoformat(task["due_date"])
            )
            created = datetime.fromisoformat(row.created_at).date()
            if not assigned <= entry <= released or created != entry:
                raise ValueError("Time-entry dates are outside the assignment window")
            if Decimal(row.hours) <= 0:
                raise ValueError("Time-entry hours must be positive")

    def _validate_required_join_cases(self, tables: dict[str, Any]) -> None:
        projects = tables["projects"]
        tasks = tables["tasks"]
        resources = tables["resources"]
        assignments = tables["task_resources"]
        milestones = tables["milestones"]
        entries = tables["time_entries"]
        if not (set(projects["project_id"]) - set(tasks["project_id"])):
            raise ValueError("PM base data lacks a project without tasks")
        if set(tasks["task_id"]) != set(assignments["task_id"]):
            raise ValueError("PM assignments do not cover every task")
        if set(resources["resource_id"]) != set(assignments["resource_id"]):
            raise ValueError("PM assignments do not cover every resource")
        if set(projects["project_id"]) != set(milestones["project_id"]):
            raise ValueError("PM milestones do not cover every project")
        if assignments.groupby("task_id")["resource_id"].nunique().max() < 2:
            raise ValueError("PM bridge lacks a task with multiple resources")
        if assignments.groupby("resource_id")["task_id"].nunique().max() < 2:
            raise ValueError("PM bridge lacks a resource with multiple tasks")
        assignment_pairs = set(
            assignments[["task_id", "resource_id"]].itertuples(index=False, name=None)
        )
        entry_pairs = set(
            entries[["task_id", "resource_id"]].itertuples(index=False, name=None)
        )
        if assignment_pairs - entry_pairs:
            raise ValueError("PM time entries do not cover every assignment")

    def _validate_currency(self, tables: dict[str, Any]) -> None:
        currency = self.config["business_mappings"]["currency_code"]
        for table_name in ("projects", "resources"):
            if set(tables[table_name]["currency_code"]) != {currency}:
                raise ValueError(f"{table_name} contains non-USD currency values")

    def _validate_clean_stage(self, tables: dict[str, Any]) -> None:
        targets = self.config["imperfection_targets"]
        for name in ("open_ended_projects", "open_ended_tasks"):
            target = targets[name]
            if _has_blanks(tables[target["table"]][target["field"]]):
                raise ValueError(f"Base PM data already contains {name} NULLs")

        outlier = targets["task_estimate_outliers"]
        clean_maximum = Decimal(
            str(
                self.config["generation_rules"]["tasks"]["estimate_hours"][
                    "maximum_amount"
                ]
            )
        )
        populated = [
            Decimal(str(value))
            for value in tables[outlier["table"]][outlier["field"]]
            if not _is_blank(value)
        ]
        if any(value > clean_maximum for value in populated):
            raise ValueError("Base PM data already contains task estimate outliers")

        duplicate = targets["near_duplicate_time_entries"]
        if (
            tables[duplicate["table"]]
            .duplicated(duplicate["business_key_fields"])
            .any()
        ):
            raise ValueError("Base PM data contains near-duplicate business keys")

        boundary_values = set(self.base_config["imperfections"]["boundary_dates"])
        boundary = targets["coordinated_boundary_dates"]
        for qualified in boundary["targets"]:
            table_name, field_name = qualified.split(".")
            values = {
                str(value).split("T", 1)[0]
                for value in tables[table_name][field_name]
                if not _is_blank(value)
            }
            if values & boundary_values:
                raise ValueError(
                    f"Base PM data already contains boundary dates in {qualified}"
                )


def validate_project_management_generated_tables(
    generator: DeterministicGenerator,
    tables: dict[str, Any],
) -> None:
    """Run all immediate clean PM table guards or raise on first failure."""

    ProjectManagementGeneratedTablesValidator(generator).validate(tables)


def _validate_fixed_decimal(
    value: Any,
    precision: int,
    scale: int,
    context: str,
) -> None:
    text = str(value)
    if re.fullmatch(rf"-?\d+\.\d{{{scale}}}", text) is None:
        raise ValueError(f"{context} must use fixed scale {scale}")
    try:
        decimal_value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"{context} contains an invalid decimal") from exc
    digits = len(decimal_value.as_tuple().digits)
    if digits > precision:
        raise ValueError(f"{context} exceeds precision {precision}")


def _has_blanks(series: Any) -> bool:
    return bool(series.isna().any() or series.eq("").any())


def _is_blank(value: Any) -> bool:
    return value is None or value == "" or value != value


__all__ = [
    "ProjectManagementGeneratedTablesValidator",
    "validate_project_management_generated_tables",
]
