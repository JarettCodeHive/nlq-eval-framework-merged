"""Controlled imperfection injection for Project Management data."""

from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from decimal import Decimal
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.distributions import format_fixed_decimal
from generators.core.imperfections import count_from_pct
from generators.core.imperfections import inject_fixed_scale_outliers
from generators.core.progress import ProgressReporter
from generators.project_management.config import load_project_management_config
from generators.project_management.config import settings_for_profile
from generators.project_management.distributions import (
    ProjectManagementDistributionApplier,
)
from generators.project_management.validators.config import (
    validate_project_management_config,
)
from generators.project_management.validators.distributed_tables import (
    validate_project_management_distributed_tables,
)
from generators.project_management.validators.imperfect_tables import (
    validate_project_management_imperfect_tables,
)


class ProjectManagementImperfectionInjector:
    """Inject approved PM defects while preserving relational coherence.

    The stage starts from validated distributed tables and introduces fresh-PK
    near-duplicate work logs, independently measured open-ended project and
    task ranges, fixed-scale task-estimate outliers, and coordinated boundary
    date scenarios. Boundary movement updates complete project/task paths so
    assignments, milestones, and work logs remain chronological.
    """

    def __init__(
        self,
        generator: DeterministicGenerator,
        progress: ProgressReporter | None = None,
    ) -> None:
        if generator.settings.domain != "project_management":
            raise ValueError(
                "ProjectManagementImperfectionInjector only supports the "
                "project_management domain"
            )
        validate_project_management_config()
        self.generator = generator
        self.settings = generator.settings
        self.config = self.settings.imperfections
        self.project_management_config = load_project_management_config()
        self.targets = self.project_management_config["imperfection_targets"]
        self.progress = progress

    @classmethod
    def for_profile(
        cls,
        profile: str,
        progress: ProgressReporter | None = None,
    ) -> "ProjectManagementImperfectionInjector":
        """Create an injector from validated PM profile configuration."""

        return cls(
            DeterministicGenerator(settings_for_profile(profile)),
            progress=progress,
        )

    def generate_imperfect_tables(self) -> dict[str, Any]:
        """Generate fresh distributed PM tables and inject imperfections."""

        distributed = ProjectManagementDistributionApplier(
            self.generator,
            progress=self.progress,
        ).generate_distributed_tables()
        return self.apply_to_tables(distributed)

    def apply_to_tables(self, tables: dict[str, Any]) -> dict[str, Any]:
        """Return imperfect deep copies of valid distributed PM tables."""

        self._report("Injecting controlled Project Management imperfections")
        validate_project_management_distributed_tables(self.generator, tables)
        imperfect = {
            table_name: table.copy(deep=True) for table_name, table in tables.items()
        }

        imperfect["time_entries"] = self._inject_near_duplicate_time_entries(
            imperfect["time_entries"]
        )
        self._report("Injected near-duplicate time entries")

        project_nulls = self._inject_open_ended_projects(imperfect["projects"])
        self._report(f"Injected {project_nulls} open-ended projects")
        task_nulls = self._inject_open_ended_tasks(imperfect["tasks"])
        self._report(f"Injected {task_nulls} open-ended tasks")

        outlier = self.targets["task_estimate_outliers"]
        imperfect["tasks"] = inject_fixed_scale_outliers(
            imperfect["tasks"],
            outlier["field"],
            self.generator.rng_for(
                "imperfections:project_management:tasks:estimate_outliers"
            ),
            float(self.config["outlier_pct"]),
            min_outlier=int(outlier["minimum_value"]),
            max_outlier=int(outlier["maximum_value"]),
            scale=int(outlier["scale"]),
        )
        self._report("Injected task-estimate outliers")

        self._inject_coordinated_boundary_dates(imperfect)
        self._report("Injected coordinated PM boundary dates")
        self._report("Validating imperfect Project Management tables")
        validate_project_management_imperfect_tables(
            self.generator,
            imperfect,
            source_tables=tables,
        )
        self._report("Project Management imperfection injection complete")
        return imperfect

    def _inject_near_duplicate_time_entries(self, entries: Any) -> Any:
        """Append logical work-log duplicates with fresh sequential IDs."""

        duplicate_count = count_from_pct(
            len(entries), float(self.config["duplicate_pct"])
        )
        result = entries.copy(deep=True)
        if duplicate_count == 0:
            return result

        rng = self.generator.rng_for(
            "imperfections:project_management:time_entries:duplicates"
        )
        positions = rng.choice(
            entries.index.to_numpy(),
            size=duplicate_count,
            replace=False,
        )
        duplicates = entries.loc[positions].copy(deep=True).reset_index(drop=True)
        start_id = int(entries["entry_id"].max()) + 1
        duplicates["entry_id"] = range(start_id, start_id + duplicate_count)
        hours = self.project_management_config["generation_rules"]["time_entries"][
            "hours"
        ]
        duplicates["hours"] = [
            _near_duplicate_decimal(
                value,
                minimum_units=int(hours["minimum_units"]),
                maximum_units=int(hours["maximum_units"]),
                scale=int(hours["scale"]),
            )
            for value in duplicates["hours"]
        ]

        pd = _require_pandas()
        return pd.concat([result, duplicates], ignore_index=True)

    def _inject_open_ended_projects(self, projects: Any) -> int:
        """Null eligible project ends at the configured independent rate."""

        target = self.targets["open_ended_projects"]
        count = count_from_pct(len(projects), float(self.config["null_pct"]))
        eligible = projects.index[
            projects["status"].isin(target["eligible_statuses"])
            & projects[target["field"]].ne("")
        ].to_numpy()
        if count > len(eligible):
            raise ValueError("PM project NULL target exceeds eligible capacity")
        rng = self.generator.rng_for(
            "imperfections:project_management:projects:end_date_nulls"
        )
        selected = rng.choice(eligible, size=count, replace=False)
        projects[target["field"]] = projects[target["field"]].astype("object")
        projects.loc[selected, target["field"]] = ""
        return count

    def _inject_open_ended_tasks(self, tasks: Any) -> int:
        """Null eligible task due dates at the configured independent rate."""

        target = self.targets["open_ended_tasks"]
        count = count_from_pct(len(tasks), float(self.config["null_pct"]))
        eligible = tasks.index[
            tasks["status"].isin(target["eligible_statuses"])
            & tasks[target["field"]].ne("")
        ].to_numpy()
        if count > len(eligible):
            raise ValueError("PM task NULL target exceeds eligible capacity")
        rng = self.generator.rng_for(
            "imperfections:project_management:tasks:due_date_nulls"
        )
        selected = rng.choice(eligible, size=count, replace=False)
        tasks[target["field"]] = tasks[target["field"]].astype("object")
        tasks.loc[selected, target["field"]] = ""
        return count

    def _inject_coordinated_boundary_dates(
        self,
        tables: dict[str, Any],
    ) -> None:
        """Place every boundary on coherent start and due-date paths.

        A start-path project is shifted as one unit, after which one task,
        assignment, milestone, and unique work log are anchored to the exact
        boundary. A different project is shifted so one populated task due date
        lands on that boundary. Distinct projects prevent the two scenarios
        from imposing contradictory windows.
        """

        boundaries = [
            date.fromisoformat(value) for value in self.config["boundary_dates"]
        ]
        if not boundaries:
            return
        project_ids = _task_bearing_project_ids(tables)
        required = len(boundaries) * 2
        if len(project_ids) < required:
            raise ValueError(
                "PM boundary injection requires two task-bearing projects per value"
            )

        start_projects = project_ids[: len(boundaries)]
        due_projects = project_ids[len(boundaries) : required]
        for boundary, project_id in zip(boundaries, start_projects, strict=True):
            self._inject_start_path_boundary(tables, project_id, boundary)
        for boundary, project_id in zip(boundaries, due_projects, strict=True):
            self._inject_due_path_boundary(tables, project_id, boundary)

    def _inject_start_path_boundary(
        self,
        tables: dict[str, Any],
        project_id: int,
        boundary: date,
    ) -> None:
        projects = tables["projects"]
        project_position = int(projects.index[projects["project_id"] == project_id][0])
        old_start = date.fromisoformat(projects.at[project_position, "start_date"])
        _shift_project_path(tables, project_id, boundary - old_start)

        tasks = tables["tasks"]
        task_candidates = tasks.index[
            (tasks["project_id"] == project_id) & tasks["due_date"].ne("")
        ]
        if task_candidates.empty:
            raise ValueError("PM boundary project has no populated task window")
        task_position = int(task_candidates[0])
        task_id = int(tasks.at[task_position, "task_id"])
        tasks.at[task_position, "start_date"] = boundary.isoformat()
        tasks.at[task_position, "created_at"] = _timestamp_on_or_before(
            tasks.at[task_position, "created_at"], boundary
        )

        entries = tables["time_entries"]
        keys = self.targets["near_duplicate_time_entries"]["business_key_fields"]
        duplicated = entries.duplicated(keys, keep=False)
        task_entries = entries[(entries["task_id"] == task_id) & ~duplicated]
        natural = task_entries.index[task_entries["entry_date"] == boundary.isoformat()]
        if not natural.empty:
            entry_position = int(natural[0])
        else:
            occupied = set(
                entries.loc[
                    (entries["task_id"] == task_id)
                    & (entries["entry_date"] == boundary.isoformat()),
                    ["resource_id", "work_type"],
                ].itertuples(index=False, name=None)
            )
            safe = task_entries.index[
                [
                    (row.resource_id, row.work_type) not in occupied
                    for row in task_entries.itertuples(index=False)
                ]
            ]
            if safe.empty:
                raise ValueError("PM boundary task has no collision-free work log")
            entry_position = int(safe[0])
        if task_entries.empty:
            raise ValueError("PM boundary assignment has no unique work log")
        resource_id = int(entries.at[entry_position, "resource_id"])

        assignments = tables["task_resources"]
        assignment_position = int(
            assignments.index[
                (assignments["task_id"] == task_id)
                & (assignments["resource_id"] == resource_id)
            ][0]
        )
        assignments.at[assignment_position, "assigned_at"] = boundary.isoformat()
        assignments.at[assignment_position, "created_at"] = _timestamp_on_or_before(
            assignments.at[assignment_position, "created_at"], boundary
        )

        entries.at[entry_position, "entry_date"] = boundary.isoformat()
        entries.at[entry_position, "created_at"] = _timestamp_on_date(
            entries.at[entry_position, "created_at"], boundary
        )

        milestones = tables["milestones"]
        milestone_position = int(
            milestones.index[milestones["project_id"] == project_id][0]
        )
        milestones.at[milestone_position, "planned_date"] = boundary.isoformat()
        milestones.at[milestone_position, "created_at"] = _timestamp_on_or_before(
            milestones.at[milestone_position, "created_at"], boundary
        )

    def _inject_due_path_boundary(
        self,
        tables: dict[str, Any],
        project_id: int,
        boundary: date,
    ) -> None:
        tasks = tables["tasks"]
        candidates = tasks.index[
            (tasks["project_id"] == project_id) & tasks["due_date"].ne("")
        ]
        if candidates.empty:
            raise ValueError("PM boundary due path has no populated task due date")
        task_position = int(candidates[0])
        old_due = date.fromisoformat(tasks.at[task_position, "due_date"])
        _shift_project_path(tables, project_id, boundary - old_due)
        if tables["tasks"].at[task_position, "due_date"] != boundary.isoformat():
            raise ValueError("PM boundary due-date shift did not reach its target")

    def _report(self, message: str) -> None:
        if self.progress is not None:
            self.progress.report(message)


def _task_bearing_project_ids(tables: dict[str, Any]) -> list[int]:
    """Return deterministic projects with tasks, milestones, and due dates."""

    projects_with_milestones = set(
        int(value) for value in tables["milestones"]["project_id"]
    )
    tasks = tables["tasks"]
    candidates: list[int] = []
    for project_id in tables["projects"]["project_id"]:
        project_tasks = tasks[
            (tasks["project_id"] == project_id) & tasks["due_date"].ne("")
        ]
        if not project_tasks.empty and int(project_id) in projects_with_milestones:
            candidates.append(int(project_id))
    return candidates


def _shift_project_path(
    tables: dict[str, Any],
    project_id: int,
    delta: timedelta,
) -> None:
    """Shift one complete project hierarchy by an exact calendar delta."""

    projects = tables["projects"]
    project_mask = projects["project_id"] == project_id
    _shift_date_column(projects, project_mask, "start_date", delta)
    _shift_date_column(projects, project_mask, "end_date", delta)
    _shift_timestamp_column(projects, project_mask, "created_at", delta)

    tasks = tables["tasks"]
    task_mask = tasks["project_id"] == project_id
    task_ids = set(int(value) for value in tasks.loc[task_mask, "task_id"])
    for field in ("start_date", "due_date", "completed_date"):
        _shift_date_column(tasks, task_mask, field, delta)
    _shift_timestamp_column(tasks, task_mask, "created_at", delta)

    assignments = tables["task_resources"]
    assignment_mask = assignments["task_id"].isin(task_ids)
    for field in ("assigned_at", "released_at"):
        _shift_date_column(assignments, assignment_mask, field, delta)
    _shift_timestamp_column(assignments, assignment_mask, "created_at", delta)

    milestones = tables["milestones"]
    milestone_mask = milestones["project_id"] == project_id
    for field in ("planned_date", "actual_date"):
        _shift_date_column(milestones, milestone_mask, field, delta)
    _shift_timestamp_column(milestones, milestone_mask, "created_at", delta)

    entries = tables["time_entries"]
    entry_mask = entries["task_id"].isin(task_ids)
    _shift_date_column(entries, entry_mask, "entry_date", delta)
    _shift_timestamp_column(entries, entry_mask, "created_at", delta)


def _shift_date_column(
    table: Any,
    mask: Any,
    field: str,
    delta: timedelta,
) -> None:
    for position in table.index[mask]:
        value = table.at[position, field]
        if not _is_blank(value):
            table.at[position, field] = (
                date.fromisoformat(str(value)) + delta
            ).isoformat()


def _shift_timestamp_column(
    table: Any,
    mask: Any,
    field: str,
    delta: timedelta,
) -> None:
    for position in table.index[mask]:
        value = table.at[position, field]
        if not _is_blank(value):
            table.at[position, field] = _format_timestamp(
                datetime.fromisoformat(str(value)) + delta
            )


def _near_duplicate_decimal(
    value: Any,
    minimum_units: int,
    maximum_units: int,
    scale: int,
) -> str:
    """Move one fixed-scale value by one minor unit inside its clean range."""

    multiplier = 10**scale
    units = int(Decimal(str(value)) * multiplier)
    changed = units - 1 if units >= maximum_units else units + 1
    changed = min(max(changed, minimum_units), maximum_units)
    return format_fixed_decimal(changed, scale)


def _timestamp_on_or_before(value: Any, upper: date) -> str:
    parsed = datetime.fromisoformat(str(value))
    if parsed.date() <= upper:
        return _format_timestamp(parsed)
    return _format_timestamp(datetime.combine(upper, time.min))


def _timestamp_on_date(value: Any, target: date) -> str:
    parsed = datetime.fromisoformat(str(value))
    return _format_timestamp(datetime.combine(target, parsed.time()))


def _format_timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S")


def _is_blank(value: Any) -> bool:
    return value is None or value == "" or value != value


def _require_pandas() -> Any:
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError(
            "pandas is required for Project Management imperfections"
        ) from exc
    return pd


__all__ = ["ProjectManagementImperfectionInjector"]
