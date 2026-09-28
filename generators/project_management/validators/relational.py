"""Full relational and temporal validation for Project Management datasets."""

from __future__ import annotations

from datetime import date
from datetime import datetime
from decimal import Decimal
from decimal import InvalidOperation
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.imperfections import count_from_pct
from generators.core.integrity import IntegrityCheckResult
from generators.core.integrity import assert_all_passed
from generators.core.integrity import empty_count
from generators.core.integrity import failed
from generators.core.integrity import non_empty_values
from generators.core.integrity import passed
from generators.core.schema_contract import primary_key_fields
from generators.project_management.config import load_project_management_config
from generators.project_management.config import settings_for_profile
from generators.project_management.imperfections import (
    ProjectManagementImperfectionInjector,
)
from generators.project_management.validators.config import (
    validate_project_management_config,
)


class ProjectManagementRelationalValidator:
    """Report complete relational and date validity for final PM tables."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "project_management":
            raise ValueError(
                "ProjectManagementRelationalValidator only supports "
                "project_management"
            )
        validate_project_management_config()
        self.generator = generator
        self.settings = generator.settings
        self.pm_config = load_project_management_config()
        self.rules = self.pm_config["generation_rules"]

    @classmethod
    def for_profile(cls, profile: str) -> "ProjectManagementRelationalValidator":
        """Create a relational validator from validated PM configuration."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def generate_and_validate(self) -> list[IntegrityCheckResult]:
        """Generate final imperfect PM tables and validate them."""

        tables = ProjectManagementImperfectionInjector(
            self.generator
        ).generate_imperfect_tables()
        return self.validate_tables(tables)

    def validate_tables(self, tables: dict[str, Any]) -> list[IntegrityCheckResult]:
        """Return every safely applicable PM integrity check."""

        results = self._validate_table_contract(tables)
        if any(not result.passed for result in results):
            return results

        column_results = self._validate_columns(tables)
        results.extend(column_results)
        if any(not result.passed for result in column_results):
            return results

        prerequisite_results: list[IntegrityCheckResult] = []
        prerequisite_results.extend(self._validate_row_counts(tables))
        prerequisite_results.extend(self._validate_keys(tables))
        prerequisite_results.extend(self._validate_required_fields(tables))
        prerequisite_results.extend(self._validate_foreign_keys(tables))
        results.extend(prerequisite_results)
        # Semantic lookups assume valid keys and complete required values.
        if any(not result.passed for result in prerequisite_results):
            return results
        results.extend(self._validate_domains_and_currency(tables))
        results.extend(self._validate_decimal_fields(tables))
        results.extend(self._validate_projects_and_tasks(tables))
        results.extend(self._validate_assignments(tables))
        results.extend(self._validate_milestones(tables))
        results.extend(self._validate_time_entries(tables))
        results.extend(self._validate_join_and_range_preconditions(tables))
        return results

    def validate_or_raise(self, tables: dict[str, Any]) -> list[IntegrityCheckResult]:
        """Validate tables and raise one combined error for all failures."""

        results = self.validate_tables(tables)
        assert_all_passed(results)
        return results

    def _validate_table_contract(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        expected = self.settings.table_order
        actual = tuple(tables)
        results = [
            _result(
                "schema.table_order",
                actual == expected,
                "all tables present in dependency order",
                f"expected {list(expected)}, got {list(actual)}",
            )
        ]
        for table_name in expected:
            results.append(
                _result(
                    f"{table_name}.present",
                    table_name in tables,
                    "table present",
                    "table missing",
                )
            )
        extra = sorted(set(tables) - set(expected))
        results.append(
            _result(
                "schema.unexpected_tables",
                not extra,
                "no unexpected tables",
                f"unexpected tables: {extra}",
            )
        )
        return results

    def _validate_columns(self, tables: dict[str, Any]) -> list[IntegrityCheckResult]:
        results: list[IntegrityCheckResult] = []
        for table_name in self.settings.table_order:
            expected = [
                field["name"]
                for field in self.pm_config["tables"][table_name]["fields"]
            ]
            actual = tables[table_name].columns.tolist()
            results.append(
                _result(
                    f"{table_name}.columns",
                    actual == expected,
                    "columns match config order",
                    f"expected {expected}, got {actual}",
                )
            )
        return results

    def _validate_row_counts(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        expected_counts = {
            table_name: self.generator.row_count(table_name)
            for table_name in self.settings.table_order
        }
        expected_counts["time_entries"] += count_from_pct(
            expected_counts["time_entries"],
            float(self.settings.imperfections["duplicate_pct"]),
        )
        results = [
            _result(
                f"{table_name}.row_count",
                len(tables[table_name]) == expected,
                f"final row count {expected}",
                f"expected {expected}, got {len(tables[table_name])}",
            )
            for table_name, expected in expected_counts.items()
        ]
        results.extend(
            _result(
                f"{table_name}.row_cap",
                len(tables[table_name]) <= self.settings.max_rows_per_table,
                f"within row cap {self.settings.max_rows_per_table}",
                f"{len(tables[table_name])} rows exceed row cap",
            )
            for table_name in self.settings.table_order
        )
        return results

    def _validate_keys(self, tables: dict[str, Any]) -> list[IntegrityCheckResult]:
        results: list[IntegrityCheckResult] = []
        for table_name in self.settings.table_order:
            config = self.pm_config["tables"][table_name]
            keys = primary_key_fields(config)
            blank_columns = [
                field for field in keys if empty_count(tables[table_name][field])
            ]
            duplicate_count = int(tables[table_name].duplicated(keys).sum())
            results.append(
                _result(
                    f"{table_name}.primary_key",
                    not blank_columns and duplicate_count == 0,
                    "primary key populated and unique",
                    f"blank columns={blank_columns}, duplicate rows={duplicate_count}",
                )
            )
        project_code_duplicates = int(tables["projects"]["project_code"].duplicated().sum())
        results.append(
            _result(
                "projects.project_code.unique",
                project_code_duplicates == 0,
                "project codes unique",
                f"{project_code_duplicates} duplicate project code(s)",
            )
        )
        return results

    def _validate_required_fields(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        results: list[IntegrityCheckResult] = []
        for table_name in self.settings.table_order:
            for field in self.pm_config["tables"][table_name]["fields"]:
                if field["nullable"]:
                    continue
                name = field["name"]
                blanks = empty_count(tables[table_name][name])
                results.append(
                    _result(
                        f"{table_name}.{name}.not_null",
                        blanks == 0,
                        "no blank values",
                        f"{blanks} blank value(s)",
                    )
                )
        return results

    def _validate_foreign_keys(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        results: list[IntegrityCheckResult] = []
        for relationship in self.pm_config["relationships"]:
            if relationship["relationship_type"] != "foreign_key":
                continue
            parent_table = relationship["parent_table"]
            parent_field = relationship["parent_field"]
            child_table = relationship["child_table"]
            child_field = relationship["child_field"]
            allowed = non_empty_values(tables[parent_table][parent_field])
            actual = non_empty_values(tables[child_table][child_field])
            invalid = sorted(actual - allowed)
            results.append(
                _result(
                    f"{child_table}.{child_field}.fk",
                    not invalid,
                    "all references valid",
                    f"invalid references: {invalid[:5]}",
                )
            )
        return results

    def _validate_domains_and_currency(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        values = self.pm_config["domain_values"]
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
        )
        results: list[IntegrityCheckResult] = []
        for table_name, field_name, values_name in memberships:
            actual = non_empty_values(tables[table_name][field_name])
            invalid = sorted(actual - set(values[values_name]))
            results.append(
                _result(
                    f"{table_name}.{field_name}.domain",
                    not invalid,
                    "all populated values are configured",
                    f"unknown values: {invalid[:5]}",
                )
            )
        expected_currency = self.pm_config["business_mappings"]["currency_code"]
        for table_name in ("projects", "resources"):
            actual = set(tables[table_name]["currency_code"].astype(str))
            results.append(
                _result(
                    f"{table_name}.currency_code",
                    actual == {expected_currency},
                    f"all values use {expected_currency}",
                    f"expected only {expected_currency}, got {sorted(actual)}",
                )
            )
        return results

    def _validate_decimal_fields(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        results: list[IntegrityCheckResult] = []
        non_negative = {
            ("projects", "budget_amount"),
            ("resources", "hourly_rate"),
            ("tasks", "estimate_hours"),
        }
        positive = {
            ("task_resources", "allocation_pct"),
            ("time_entries", "hours"),
        }
        for table_name in self.settings.table_order:
            for field in self.pm_config["tables"][table_name]["fields"]:
                if field["type"] != "decimal":
                    continue
                field_name = field["name"]
                invalid_format = 0
                invalid_sign = 0
                for value in tables[table_name][field_name]:
                    if _is_blank(value):
                        continue
                    amount = _fixed_decimal_or_none(
                        value,
                        int(field["precision"]),
                        int(field["scale"]),
                    )
                    if amount is None:
                        invalid_format += 1
                        continue
                    if (table_name, field_name) in non_negative and amount < 0:
                        invalid_sign += 1
                    if (table_name, field_name) in positive and amount <= 0:
                        invalid_sign += 1
                qualified = f"{table_name}.{field_name}"
                results.append(_count_result(f"{qualified}.fixed_decimal", invalid_format))
                results.append(_count_result(f"{qualified}.non_negative", invalid_sign))
        return results

    def _validate_projects_and_tasks(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        projects = tables["projects"]
        tasks = tables["tasks"]
        project_lookup = projects.set_index("project_id")
        project_chronology = 0
        for row in projects.itertuples(index=False):
            created = _timestamp_date(row.created_at)
            start = _date(row.start_date)
            end = _optional_date(row.end_date)
            project_chronology += created > start or (end is not None and start > end)

        completed_status = self.pm_config["business_mappings"]["task_status_dates"][
            "completed_status"
        ]
        task_chronology = 0
        task_parent_window = 0
        task_status_dates = 0
        for row in tasks.itertuples(index=False):
            project = project_lookup.loc[row.project_id]
            project_start = _date(project["start_date"])
            project_end = _optional_date(project["end_date"])
            created = _timestamp_date(row.created_at)
            start = _date(row.start_date)
            due = _optional_date(row.due_date)
            completed = _optional_date(row.completed_date)
            task_chronology += (
                created > start
                or (due is not None and start > due)
                or (completed is not None and start > completed)
            )
            task_parent_window += start < project_start
            task_parent_window += project_end is not None and start > project_end
            task_parent_window += (
                project_end is not None and due is not None and due > project_end
            )
            task_parent_window += (
                project_end is not None
                and completed is not None
                and completed > project_end
            )
            task_status_dates += (row.status == completed_status) != (
                completed is not None
            )
        return [
            _temporal_result("projects.chronology", project_chronology),
            _temporal_result("tasks.chronology", task_chronology),
            _temporal_result("tasks.parent_project_window", task_parent_window),
            _count_result("tasks.status_date_consistency", task_status_dates),
        ]

    def _validate_assignments(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        assignments = tables["task_resources"]
        tasks = tables["tasks"].set_index("task_id")
        pair_duplicates = int(assignments.duplicated(["task_id", "resource_id"]).sum())
        chronology = 0
        parent_window = 0
        for row in assignments.itertuples(index=False):
            task = tasks.loc[row.task_id]
            task_start = _date(task["start_date"])
            task_due = _optional_date(task["due_date"])
            assigned = _date(row.assigned_at)
            released = _optional_date(row.released_at)
            created = _timestamp_date(row.created_at)
            chronology += created > assigned
            chronology += released is not None and assigned > released
            parent_window += assigned < task_start
            parent_window += task_due is not None and assigned > task_due
            parent_window += (
                task_due is not None and released is not None and released > task_due
            )

        expected_total = Decimal(self.pm_config["decimal_policy"]["allocation_total"])
        totals = (
            assignments.assign(
                _allocation=assignments["allocation_pct"].map(Decimal)
            )
            .groupby("task_id")["_allocation"]
            .sum()
        )
        invalid_totals = int((totals != expected_total).sum())
        task_coverage = set(tasks.index) - set(assignments["task_id"])
        resource_coverage = set(tables["resources"]["resource_id"]) - set(
            assignments["resource_id"]
        )
        multi_resource = int(assignments.groupby("task_id")["resource_id"].nunique().max())
        multi_task = int(assignments.groupby("resource_id")["task_id"].nunique().max())
        return [
            _count_result("task_resources.unique_pairs", pair_duplicates),
            _temporal_result("task_resources.chronology", chronology),
            _temporal_result("task_resources.task_window", parent_window),
            _count_result("task_resources.allocation_total", invalid_totals),
            _result(
                "task_resources.task_coverage",
                not task_coverage,
                "every task has an assignment",
                f"unassigned task IDs: {sorted(task_coverage)[:5]}",
            ),
            _result(
                "task_resources.resource_coverage",
                not resource_coverage,
                "every resource has an assignment",
                f"unassigned resource IDs: {sorted(resource_coverage)[:5]}",
            ),
            _result(
                "task_resources.many_to_many",
                multi_resource >= 2 and multi_task >= 2,
                "bridge contains both many-to-many cardinalities",
                f"max resources/task={multi_resource}, max tasks/resource={multi_task}",
            ),
        ]

    def _validate_milestones(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        projects = tables["projects"].set_index("project_id")
        milestones = tables["milestones"]
        completed_status = self.pm_config["business_mappings"][
            "milestone_completion"
        ]["completed_status"]
        chronology = 0
        parent_window = 0
        status_dates = 0
        for row in milestones.itertuples(index=False):
            project = projects.loc[row.project_id]
            project_start = _date(project["start_date"])
            project_end = _optional_date(project["end_date"])
            created = _timestamp_date(row.created_at)
            planned = _date(row.planned_date)
            actual = _optional_date(row.actual_date)
            chronology += created > planned
            parent_window += planned < project_start
            parent_window += project_end is not None and planned > project_end
            parent_window += (
                actual is not None
                and (actual < project_start or (project_end is not None and actual > project_end))
            )
            status_dates += (row.status == completed_status) != (actual is not None)
        project_coverage = set(projects.index) - set(milestones["project_id"])
        return [
            _temporal_result("milestones.chronology", chronology),
            _temporal_result("milestones.project_window", parent_window),
            _count_result("milestones.status_date_consistency", status_dates),
            _result(
                "milestones.project_coverage",
                not project_coverage,
                "every project has a milestone",
                f"projects without milestones: {sorted(project_coverage)[:5]}",
            ),
        ]

    def _validate_time_entries(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        entries = tables["time_entries"]
        assignments = tables["task_resources"]
        tasks = tables["tasks"].set_index("task_id")
        projects = tables["projects"].set_index("project_id")
        assignment_lookup = {
            (int(row.task_id), int(row.resource_id)): row
            for row in assignments.itertuples(index=False)
        }
        entry_pairs = set(
            entries[["task_id", "resource_id"]].itertuples(index=False, name=None)
        )
        assignment_pairs = set(assignment_lookup)
        invalid_pairs = entry_pairs - assignment_pairs
        missing_pairs = assignment_pairs - entry_pairs
        chronology = 0
        if not invalid_pairs:
            for row in entries.itertuples(index=False):
                assignment = assignment_lookup[(row.task_id, row.resource_id)]
                task = tasks.loc[row.task_id]
                project = projects.loc[task["project_id"]]
                entry = _date(row.entry_date)
                assigned = _date(assignment.assigned_at)
                released = _optional_date(assignment.released_at)
                due = _optional_date(task["due_date"])
                project_end = _optional_date(project["end_date"])
                upper = released or due or project_end
                chronology += entry < assigned
                chronology += upper is not None and entry > upper
                chronology += _timestamp_date(row.created_at) != entry
        return [
            _result(
                "time_entries.assignment_membership",
                not invalid_pairs,
                "every task/resource pair has a declared assignment",
                f"undeclared pairs: {sorted(invalid_pairs)[:5]}",
            ),
            _result(
                "time_entries.assignment_coverage",
                not missing_pairs,
                "every assignment has at least one time entry",
                f"assignments without entries: {sorted(missing_pairs)[:5]}",
            ),
            _temporal_result("time_entries.assignment_window", chronology),
        ]

    def _validate_join_and_range_preconditions(
        self,
        tables: dict[str, Any],
    ) -> list[IntegrityCheckResult]:
        projects = tables["projects"]
        tasks = tables["tasks"]
        assignments = tables["task_resources"]
        milestones = tables["milestones"]
        entries = tables["time_entries"]
        taskless = set(projects["project_id"]) - set(tasks["project_id"])
        open_projects = empty_count(projects["end_date"])
        open_tasks = empty_count(tasks["due_date"])
        project_overlap = _has_overlapping_ranges(
            projects,
            group_field=None,
            start_field="start_date",
            end_field="end_date",
        )
        task_overlap = _has_overlapping_ranges(
            tasks,
            group_field="project_id",
            start_field="start_date",
            end_field="due_date",
        )
        assignment_overlap = _has_overlapping_ranges(
            assignments,
            group_field="resource_id",
            start_field="assigned_at",
            end_field="released_at",
        )
        three_table_path = bool(
            set(projects["project_id"]) & set(tasks["project_id"])
            and set(tasks["task_id"]) & set(entries["task_id"])
        )
        return [
            _result(
                "projects.without_tasks",
                bool(taskless),
                f"{len(taskless)} project(s) support the LEFT JOIN path",
                "no project without tasks",
            ),
            _result(
                "projects.open_ended",
                open_projects > 0,
                f"{open_projects} open-ended project(s)",
                "no open-ended project",
            ),
            _result(
                "tasks.open_ended",
                open_tasks > 0,
                f"{open_tasks} open-ended task(s)",
                "no open-ended task",
            ),
            _result(
                "projects.overlapping_ranges",
                project_overlap,
                "overlapping project windows present",
                "no overlapping project windows",
            ),
            _result(
                "tasks.overlapping_ranges",
                task_overlap,
                "overlapping task windows present within a project",
                "no overlapping task windows within a project",
            ),
            _result(
                "task_resources.overlapping_ranges",
                assignment_overlap,
                "overlapping resource assignments present",
                "no overlapping resource assignments",
            ),
            _result(
                "project_task_time_entry.inner_path",
                three_table_path,
                "Project-to-Task-to-Time Entry path is non-empty",
                "Project-to-Task-to-Time Entry path is empty",
            ),
            _result(
                "milestones.completion_denominator",
                set(projects["project_id"]) == set(milestones["project_id"]),
                "every project has a non-zero milestone denominator",
                "one or more projects have no milestones",
            ),
        ]


def _fixed_decimal_or_none(value: Any, precision: int, scale: int) -> Decimal | None:
    text = str(value)
    if "." not in text or len(text.rsplit(".", 1)[1]) != scale:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    if not amount.is_finite():
        return None
    digits = text.lstrip("-").replace(".", "").lstrip("0") or "0"
    return amount if len(digits) <= precision else None


def _has_overlapping_ranges(
    table: Any,
    group_field: str | None,
    start_field: str,
    end_field: str,
) -> bool:
    groups = [(None, table)] if group_field is None else table.groupby(group_field)
    for _, group in groups:
        intervals = sorted(
            (
                (_date(row[start_field]), _optional_date(row[end_field]))
                for _, row in group.iterrows()
            ),
            key=lambda interval: interval[0],
        )
        latest_end: date | None = None
        seen = False
        for start, end in intervals:
            if seen and (latest_end is None or start <= latest_end):
                return True
            seen = True
            if latest_end is None or end is None:
                latest_end = None if end is None else end
            elif end > latest_end:
                latest_end = end
    return False


def _date(value: Any) -> date:
    return date.fromisoformat(str(value))


def _optional_date(value: Any) -> date | None:
    return None if _is_blank(value) else _date(value)


def _timestamp_date(value: Any) -> date:
    return datetime.fromisoformat(str(value)).date()


def _is_blank(value: Any) -> bool:
    return value is None or value == "" or value != value


def _result(
    check_name: str,
    condition: bool,
    passed_message: str,
    failed_message: str,
) -> IntegrityCheckResult:
    return passed(check_name, passed_message) if condition else failed(check_name, failed_message)


def _count_result(check_name: str, violation_count: int) -> IntegrityCheckResult:
    return _result(
        check_name,
        violation_count == 0,
        "no violations",
        f"{violation_count} violation(s)",
    )


def _temporal_result(check_name: str, violation_count: int) -> IntegrityCheckResult:
    return _result(
        check_name,
        violation_count == 0,
        "chronology valid",
        f"{violation_count} temporal violation(s)",
    )


__all__ = ["ProjectManagementRelationalValidator"]
