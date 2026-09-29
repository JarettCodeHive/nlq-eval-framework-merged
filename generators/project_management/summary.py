"""Human-readable Project Management summaries for the root CLI."""

from __future__ import annotations

from typing import Any

from generators.project_management.imperfections import (
    ProjectManagementImperfectionInjector,
)


def print_relationship_summary(tables: dict[str, Any]) -> None:
    """Print concise PM foreign-key, bridge, and assignment coverage."""

    projects = tables["projects"]
    resources = tables["resources"]
    tasks = tables["tasks"]
    assignments = tables["task_resources"]
    milestones = tables["milestones"]
    entries = tables["time_entries"]
    project_ids = set(projects["project_id"])
    task_ids = set(tasks["task_id"])
    resource_ids = set(resources["resource_id"])
    assignment_pairs = set(
        zip(assignments["task_id"], assignments["resource_id"], strict=True)
    )
    entry_pairs = set(zip(entries["task_id"], entries["resource_id"], strict=True))

    print("relationship_checks:")
    print(f"  tasks.project_id valid: {set(tasks['project_id']).issubset(project_ids)}")
    print(
        "  milestones.project_id valid: "
        f"{set(milestones['project_id']).issubset(project_ids)}"
    )
    print(
        "  task_resources.task_id valid: "
        f"{set(assignments['task_id']).issubset(task_ids)}"
    )
    print(
        "  task_resources.resource_id valid: "
        f"{set(assignments['resource_id']).issubset(resource_ids)}"
    )
    print(
        "  time_entry_assignment_membership: "
        f"{entry_pairs.issubset(assignment_pairs)}"
    )
    print(
        "  tasks_with_multiple_resources: "
        f"{int(assignments.groupby('task_id')['resource_id'].nunique().ge(2).sum())}"
    )
    print(
        "  resources_with_multiple_tasks: "
        f"{int(assignments.groupby('resource_id')['task_id'].nunique().ge(2).sum())}"
    )


def print_distribution_summary(tables: dict[str, Any]) -> None:
    """Print concise PM budget, work-frequency, and date measurements."""

    projects = tables["projects"]
    entries = tables["time_entries"]
    budgets = projects.loc[
        projects["budget_amount"].astype(str).ne(""), "budget_amount"
    ].astype(float)
    entry_counts = entries.groupby(["task_id", "resource_id"]).size()

    print("distribution_checks:")
    print(f"  project_budget_min: {budgets.min():.2f}")
    print(f"  project_budget_max: {budgets.max():.2f}")
    print(f"  project_budget_mean: {budgets.mean():.2f}")
    print(f"  time_entries_per_assignment_mean: {entry_counts.mean():.2f}")
    print(f"  time_entries_per_assignment_max: {entry_counts.max()}")
    print(f"  distinct_entry_dates: {entries['entry_date'].nunique()}")


def print_imperfection_summary(
    tables: dict[str, Any],
    injector: ProjectManagementImperfectionInjector,
) -> None:
    """Print concise PM imperfection measurements."""

    projects = tables["projects"]
    tasks = tables["tasks"]
    entries = tables["time_entries"]
    duplicate_rows = len(entries) - injector.generator.row_count("time_entries")
    outlier_target = injector.pm_config["imperfection_targets"][
        "task_estimate_outliers"
    ]
    minimum = float(outlier_target["minimum_value"])
    boundaries = set(injector.config["boundary_dates"])

    print("imperfection_checks:")
    print(f"  time_entry_near_duplicates: {duplicate_rows}")
    print(f"  projects.end_date_empty: {int(projects['end_date'].eq('').sum())}")
    print(f"  tasks.due_date_empty: {int(tasks['due_date'].eq('').sum())}")
    estimates = tasks.loc[
        tasks["estimate_hours"].astype(str).ne(""), "estimate_hours"
    ].astype(float)
    print(f"  task_estimate_outliers_ge_{int(minimum)}: {int(estimates.ge(minimum).sum())}")
    print(
        "  task_boundary_start_dates: "
        f"{int(tasks['start_date'].astype(str).isin(boundaries).sum())}"
    )


__all__ = [
    "print_distribution_summary",
    "print_imperfection_summary",
    "print_relationship_summary",
]
