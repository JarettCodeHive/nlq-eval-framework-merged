"""Project Management release data-dictionary generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.manifest import build_distribution_metadata
from generators.project_management.config import load_project_management_config
from generators.project_management.config import settings_for_profile
from generators.project_management.validators.config import (
    validate_project_management_config,
)


TABLE_DESCRIPTIONS = {
    "projects": "Project portfolio dimension containing schedule, status, priority, and approved budget.",
    "resources": "People and resource dimension used for staffing, rate, department, and utilization analysis.",
    "tasks": "Project work-item table containing planned dates, completion state, type, and estimated effort.",
    "task_resources": "Explicit many-to-many task staffing bridge with role, allocation, and assignment dates.",
    "milestones": "Project milestone table used for planned-versus-actual dates and completion percentages.",
    "time_entries": "Work-log fact table recording hours by declared task/resource assignment and date.",
}


FIELD_DESCRIPTIONS = {
    "projects.project_id": "Unique project identifier.",
    "projects.project_name": "Synthetic business-facing project name.",
    "projects.project_code": "Unique deterministic project code.",
    "projects.status": "Current project lifecycle status.",
    "projects.priority": "Business priority assigned to the project.",
    "projects.start_date": "Inclusive planned project start date.",
    "projects.end_date": "Planned project end; NULL means open-ended.",
    "projects.budget_amount": "Approved project budget in USD, when available.",
    "projects.currency_code": "Project budget currency; always USD.",
    "projects.created_at": "Timestamp when the project record was created.",
    "resources.resource_id": "Unique resource identifier.",
    "resources.resource_name": "Synthetic resource display name.",
    "resources.role": "Primary delivery role of the resource.",
    "resources.department": "Optional organizational department.",
    "resources.location": "Optional synthetic work location.",
    "resources.hourly_rate": "USD hourly billing or costing rate, when configured.",
    "resources.currency_code": "Resource rate currency; always USD.",
    "resources.is_active": "Whether the resource is currently active.",
    "resources.created_at": "Timestamp when the resource record was created.",
    "tasks.task_id": "Unique task identifier.",
    "tasks.project_id": "Project that owns the task.",
    "tasks.task_name": "Synthetic business-facing task name.",
    "tasks.status": "Current task execution status.",
    "tasks.task_type": "Optional classification of the work item.",
    "tasks.start_date": "Inclusive task start date within its project window.",
    "tasks.due_date": "Planned task due date; NULL means open-ended.",
    "tasks.completed_date": "Actual completion date for a completed task.",
    "tasks.estimate_hours": "Estimated effort in hours, when available.",
    "tasks.created_at": "Timestamp when the task record was created.",
    "task_resources.task_id": "Task receiving the resource assignment.",
    "task_resources.resource_id": "Resource assigned to the task.",
    "task_resources.assignment_role": "Optional role performed on this assignment.",
    "task_resources.allocation_pct": "Resource allocation percentage for the task.",
    "task_resources.assigned_at": "Inclusive assignment start date.",
    "task_resources.released_at": "Assignment release date; NULL means still active.",
    "task_resources.created_at": "Timestamp when the assignment was created.",
    "milestones.milestone_id": "Unique milestone identifier.",
    "milestones.project_id": "Project that owns the milestone.",
    "milestones.milestone_name": "Synthetic business-facing milestone name.",
    "milestones.milestone_type": "Optional milestone classification.",
    "milestones.planned_date": "Planned milestone date.",
    "milestones.actual_date": "Actual milestone date; NULL means incomplete.",
    "milestones.status": "Current milestone status.",
    "milestones.created_at": "Timestamp when the milestone was created.",
    "time_entries.entry_id": "Unique work-log identifier.",
    "time_entries.task_id": "Task against which work was logged.",
    "time_entries.resource_id": "Resource that logged the work.",
    "time_entries.entry_date": "Calendar date on which work was performed.",
    "time_entries.hours": "Positive fixed-scale hours logged.",
    "time_entries.billable": "Whether the logged work is billable.",
    "time_entries.work_type": "Classification of the logged work.",
    "time_entries.notes": "Optional synthetic work-log note.",
    "time_entries.created_at": "Timestamp associated with the work-log date.",
}


class ProjectManagementDataDictionaryGenerator:
    """Generate the PM release dictionary from validated configuration."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "project_management":
            raise ValueError(
                "ProjectManagementDataDictionaryGenerator only supports "
                "project_management"
            )
        validate_project_management_config()
        self.generator = generator
        self.settings = generator.settings
        self.pm_config = load_project_management_config()
        self._validate_description_coverage()

    @classmethod
    def for_profile(
        cls,
        profile: str,
    ) -> "ProjectManagementDataDictionaryGenerator":
        """Create a PM dictionary generator from profile configuration."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def generate_markdown(self) -> str:
        """Return deterministic Markdown for the complete PM contract."""

        lines = [
            "# Project Management Data Dictionary",
            "",
            f"Release version: `{self.settings.release_version}`",
            "",
            f"Schema source: `{self.pm_config['schema_source']}`",
            "",
            f"Fixed reference date: `{self.settings.reference_today.isoformat()}`",
            "",
            "Project Management v1.0 is USD-only and uses synthetic test data.",
            "",
            "## Tables",
            "",
        ]
        for table_name in self.settings.table_order:
            lines.extend(self._table_section(table_name))
        lines.extend(self._domain_values_section())
        lines.extend(self._distribution_section())
        lines.extend(self._relationship_section())
        lines.extend(self._business_semantics_section())
        lines.extend(self._imperfection_section())
        return "\n".join(lines).rstrip() + "\n"

    def write_release_dictionary(self) -> Path:
        """Atomically write data_dictionary.md into an unsealed release."""

        if not self.settings.is_release_profile:
            raise ValueError(
                "Project Management release data-dictionary generation requires "
                f"the full profile; got profile={self.settings.profile}"
            )
        manifest_path = self.settings.output_path / "manifest.json"
        if manifest_path.exists():
            raise FileExistsError(
                "Refusing to modify immutable release after manifest exists: "
                f"{manifest_path}"
            )
        output_path = self.settings.output_path / "data_dictionary.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_suffix(".md.tmp")
        temporary.write_text(self.generate_markdown(), encoding="utf-8", newline="\n")
        temporary.replace(output_path)
        return output_path

    def _validate_description_coverage(self) -> None:
        expected_fields = {
            f"{table_name}.{field['name']}"
            for table_name in self.settings.table_order
            for field in self.pm_config["tables"][table_name]["fields"]
        }
        if set(FIELD_DESCRIPTIONS) != expected_fields:
            raise ValueError(
                "PM field-description coverage differs from config: "
                f"missing={sorted(expected_fields - set(FIELD_DESCRIPTIONS))}, "
                f"extra={sorted(set(FIELD_DESCRIPTIONS) - expected_fields)}"
            )
        if set(TABLE_DESCRIPTIONS) != set(self.settings.table_order):
            raise ValueError("PM table-description coverage differs from table_order")

    def _table_section(self, table_name: str) -> list[str]:
        table = self.pm_config["tables"][table_name]
        lines = [
            f"### `{table_name}`",
            "",
            TABLE_DESCRIPTIONS[table_name],
            "",
            f"Role: `{table['role']}`",
            "",
            (
                f"Base row targets: dev `{table['row_targets']['dev']}`, "
                f"full `{table['row_targets']['full']}`."
            ),
            "",
            "| Column | Type | Nullable | Key role | References | Default | Business meaning | Generation behavior | NULL / imperfection behavior |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        lines.extend(self._field_row(table_name, field) for field in table["fields"])
        lines.append("")
        return lines

    def _field_row(self, table_name: str, field: dict[str, Any]) -> str:
        name = field["name"]
        reference = field.get("references")
        reference_text = (
            f"`{reference['table']}.{reference['field']}`" if reference else ""
        )
        default = field.get("default", "")
        if isinstance(default, bool):
            default = str(default).lower()
        default_text = f"`{default}`" if default != "" else ""
        key = f"`{field['key']}`" if field.get("key") else ""
        return (
            f"| `{name}` | `{_type_text(field)}` | "
            f"`{str(field['nullable']).lower()}` | {key} | {reference_text} | "
            f"{default_text} | {FIELD_DESCRIPTIONS[f'{table_name}.{name}']} | "
            f"{_generation_text(field)} | {_imperfection_text(table_name, field)} |"
        )

    def _domain_values_section(self) -> list[str]:
        lines = ["## Allowed Domain Values", ""]
        for name, values in self.pm_config["domain_values"].items():
            rendered = ", ".join(f"`{value}`" for value in values)
            lines.append(f"- {name.replace('_', ' ').title()}: {rendered}.")
        lines.extend(
            [
                "",
                "## Base Generation Rules",
                "",
                "```json",
                json.dumps(self.pm_config["generation_rules"], indent=2),
                "```",
                "",
            ]
        )
        return lines

    def _distribution_section(self) -> list[str]:
        metadata = build_distribution_metadata(
            self.pm_config["distributions"],
            self.settings.distributions,
            self.pm_config["distribution_targets"],
        )
        lines = [
            "## Distribution Configuration",
            "",
            "| Setting | Shared preset | PM overrides | Effective parameters | Targets |",
            "|---|---|---|---|---|",
        ]
        for key, spec in metadata.items():
            overrides = _compact_json(spec["overrides"]) if spec["overrides"] else "None"
            targets = ", ".join(
                _distribution_target_text(target)
                for target in spec["targets"].values()
            )
            lines.append(
                f"| `{key}` | `{spec['preset']}` | {overrides} | "
                f"{_compact_json(spec['effective_parameters'])} | {targets} |"
            )
        lines.append("")
        return lines

    def _relationship_section(self) -> list[str]:
        lines = [
            "## Relationships",
            "",
            "| Type | Parent | Child | Cardinality | Join type | Enforcement |",
            "|---|---|---|---|---|---|",
        ]
        for relationship in self.pm_config["relationships"]:
            if relationship["relationship_type"] == "foreign_key":
                parent = f"`{relationship['parent_table']}.{relationship['parent_field']}`"
                child = f"`{relationship['child_table']}.{relationship['child_field']}`"
                enforcement = "Physical FK."
            else:
                parent = "`task_resources.(task_id, resource_id)`"
                child = "`time_entries.(task_id, resource_id)`"
                enforcement = "Semantic composite membership validated after load."
            lines.append(
                f"| `{relationship['relationship_type']}` | {parent} | {child} | "
                f"`{relationship['cardinality']}` | `{relationship['join_type']}` | "
                f"{enforcement} |"
            )
        lines.extend(
            [
                "",
                "`task_resources` is the explicit Task-to-Resource many-to-many bridge.",
                "The Project-to-Task LEFT JOIN deliberately includes projects without tasks.",
                "",
            ]
        )
        return lines

    def _business_semantics_section(self) -> list[str]:
        policy = self.pm_config["decimal_policy"]
        return [
            "## Date, Decimal, and Metric Semantics",
            "",
            "- Overdue: `due_date < reference_today AND completed_date IS NULL`.",
            "- In progress: `tasks.status = 'InProgress'`.",
            "- This quarter uses an inclusive calendar-quarter start and exclusive next-quarter start for `time_entries.entry_date`.",
            "- Open-ended projects have `end_date IS NULL`; open-ended tasks have `due_date IS NULL`.",
            "- Active assignments satisfy `assigned_at <= reference_today AND (released_at IS NULL OR released_at >= reference_today)`.",
            "- Project, task, and assignment ranges deliberately include valid overlaps.",
            "- Milestone completion percentage is `100 * completed_milestones / NULLIF(total_milestones, 0)` where completion means `actual_date IS NOT NULL`.",
            f"- Decimal rounding mode is `{policy['rounding_mode']}`; calculate first and round once.",
            f"- Task allocations sum to `{policy['allocation_total']}` and monetary/rate values use USD only.",
            "- Binary floating-point arithmetic is prohibited for contracted decimal calculations.",
            "",
        ]

    def _imperfection_section(self) -> list[str]:
        values = ", ".join(f"`{value}`" for value in self.config_boundary_dates)
        target = self.pm_config["imperfection_targets"]["task_estimate_outliers"]
        return [
            "## Controlled Imperfections",
            "",
            f"- Near-duplicate time entries: `{self.settings.imperfections['duplicate_pct']}%` appended rows with fresh IDs and approved hours/notes variation.",
            f"- Open-ended project and task ranges: `{self.settings.imperfections['null_pct']}%` independently per target table.",
            f"- Task estimate outliers: `{self.settings.imperfections['outlier_pct']}%` from `{target['minimum_value']}` through `{target['maximum_value']}` at scale `{target['scale']}`.",
            f"- Coordinated boundary dates: {values}.",
            "",
            "Business-semantic NULLs such as missing budgets, rates, departments, locations, notes, and unreleased assignments remain distinct from controlled NULL-rate targets.",
            "",
        ]

    @property
    def config_boundary_dates(self) -> list[str]:
        """Return shared boundary dates for dictionary rendering."""

        return list(self.settings.imperfections["boundary_dates"])


def _type_text(field: dict[str, Any]) -> str:
    if field["type"] in {"varchar", "char"}:
        return f"{field['type']}({field['max_length']})"
    if field["type"] == "decimal":
        return f"decimal({field['precision']},{field['scale']})"
    return str(field["type"])


def _generation_text(field: dict[str, Any]) -> str:
    source = field.get("synthetic_source", "deterministic entity identifier")
    distribution = (
        f"; distribution `{field['distribution']}`" if field.get("distribution") else ""
    )
    return f"Source `{source}`{distribution}."


def _imperfection_text(table_name: str, field: dict[str, Any]) -> str:
    qualified = f"{table_name}.{field['name']}"
    behavior: list[str] = []
    if qualified == "time_entries.entry_id":
        behavior.append("Near-duplicate rows receive fresh identifiers.")
    if field.get("imperfection_target"):
        behavior.append(f"Controlled `{field['imperfection_target']}` target.")
    if field.get("null_semantics"):
        behavior.append(f"Business-state NULL: {field['null_semantics']}")
    elif field["nullable"] and not behavior:
        behavior.append("Optional business attribute; not a controlled NULL target.")
    return " ".join(behavior) or "Not an imperfection target."


def _compact_json(value: Any) -> str:
    return f"`{json.dumps(value, sort_keys=True, separators=(',', ':'))}`"


def _distribution_target_text(target: dict[str, Any]) -> str:
    if "targets" in target:
        return ", ".join(f"`{value}`" for value in target["targets"])
    if "field" in target:
        return f"`{target['table']}.{target['field']}`"
    if "group_by_fields" in target:
        fields = ", ".join(target["group_by_fields"])
        return f"`{target['table']}` grouped by `{fields}`"
    return f"`{target['table']}`"


__all__ = [
    "FIELD_DESCRIPTIONS",
    "ProjectManagementDataDictionaryGenerator",
    "TABLE_DESCRIPTIONS",
]
