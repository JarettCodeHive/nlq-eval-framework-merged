"""Deterministic distribution application for Project Management data."""

from __future__ import annotations

from collections import Counter
from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.distributions import gaussian_mixture_dates
from generators.core.distributions import gaussian_mixture_offsets
from generators.core.distributions import pareto_decimal_strings
from generators.core.distributions import poisson_weights
from generators.core.progress import ProgressReporter
from generators.project_management.config import load_project_management_config
from generators.project_management.config import settings_for_profile
from generators.project_management.generator import ProjectManagementBaseEntityGenerator
from generators.project_management.validators.config import (
    validate_project_management_config,
)
from generators.project_management.validators.distributed_tables import (
    validate_project_management_distributed_tables,
)
from generators.project_management.validators.generated_tables import (
    validate_project_management_generated_tables,
)


class ProjectManagementDistributionApplier:
    """Apply realistic PM distributions without changing table contracts.

    Populated project budgets receive bounded Pareto values, time-entry rows
    are redistributed across declared task/resource assignments with
    Poisson-derived frequencies, and configured business dates are clustered
    with a Gaussian mixture. The date pass proceeds from projects to tasks,
    assignments, milestones, and entries so every dependent window remains
    chronological and relationally valid.
    """

    def __init__(
        self,
        generator: DeterministicGenerator,
        progress: ProgressReporter | None = None,
    ) -> None:
        if generator.settings.domain != "project_management":
            raise ValueError(
                "ProjectManagementDistributionApplier only supports the "
                "project_management domain"
            )
        validate_project_management_config()
        self.generator = generator
        self.settings = generator.settings
        self.config = load_project_management_config()
        self.rules = self.config["generation_rules"]
        self.progress = progress
        self.forbidden_dates = {
            date.fromisoformat(value)
            for value in self.settings.imperfections["boundary_dates"]
        }

    @classmethod
    def for_profile(
        cls,
        profile: str,
        progress: ProgressReporter | None = None,
    ) -> "ProjectManagementDistributionApplier":
        """Create a PM distribution applier for a validated profile."""

        return cls(
            DeterministicGenerator(settings_for_profile(profile)),
            progress=progress,
        )

    def generate_distributed_tables(self) -> dict[str, Any]:
        """Generate fresh clean PM tables and apply all distributions."""

        base_tables = ProjectManagementBaseEntityGenerator(
            self.generator,
            progress=self.progress,
        ).generate_tables()
        return self.apply_to_tables(base_tables)

    def apply_to_tables(self, tables: dict[str, Any]) -> dict[str, Any]:
        """Return distributed deep copies of valid clean PM tables."""

        self._report("Applying Project Management distributions")
        validate_project_management_generated_tables(self.generator, tables)
        distributed = {
            table_name: table.copy(deep=True) for table_name, table in tables.items()
        }

        self._apply_project_budget_distribution(distributed["projects"])
        self._report("Applied Pareto project-budget distribution")
        self._apply_time_entry_frequency_distribution(distributed)
        self._report("Applied Poisson time-entry-frequency distribution")
        self._apply_date_distributions(distributed)
        self._report("Applied Gaussian-mixture date distributions")

        self._report("Validating distributed Project Management tables")
        validate_project_management_distributed_tables(
            self.generator,
            distributed,
            source_tables=tables,
        )
        self._report("Project Management distribution application complete")
        return distributed

    def _report(self, message: str) -> None:
        if self.progress is not None:
            self.progress.report(message)

    def _apply_project_budget_distribution(self, projects: Any) -> None:
        """Replace populated budgets while preserving clean business NULLs."""

        target = self.config["distribution_targets"]["project_budget"]
        spec = self.settings.distributions[target["settings_key"]]
        populated = [
            position
            for position, value in enumerate(projects[target["field"]])
            if not _is_blank(value)
        ]
        values = pareto_decimal_strings(
            self.generator.rng_for(
                "distribution:project_management:projects:budget_amount"
            ),
            count=len(populated),
            alpha=float(spec["alpha"]),
            min_amount=int(spec["min_amount"]),
            max_amount=int(spec["max_amount"]),
            scale=int(spec["scale"]),
        )
        for position, value in zip(populated, values, strict=True):
            projects.at[position, target["field"]] = value

    def _apply_time_entry_frequency_distribution(
        self,
        tables: dict[str, Any],
    ) -> None:
        """Redistribute entries over assignments and preserve exact row count."""

        assignments = list(tables["task_resources"].itertuples(index=False))
        entries = tables["time_entries"]
        target = self.config["distribution_targets"]["time_entry_frequency"]
        spec = self.settings.distributions[target["settings_key"]]
        rng = self.generator.rng_for(
            "distribution:project_management:time_entries:frequency"
        )
        counts = _exact_poisson_counts(
            rng,
            item_count=len(assignments),
            total_count=len(entries),
            lam=float(spec["lambda"]),
        )

        pairs: list[tuple[int, int]] = []
        for assignment, count in zip(assignments, counts, strict=True):
            pairs.extend(
                [(int(assignment.task_id), int(assignment.resource_id))] * int(count)
            )
        entries["task_id"] = [task_id for task_id, _ in pairs]
        entries["resource_id"] = [resource_id for _, resource_id in pairs]

    def _apply_date_distributions(self, tables: dict[str, Any]) -> None:
        """Cluster configured dates and rebuild dependent PM chronology."""

        spec = self.settings.distributions["date_clustering"]
        self._cluster_project_dates(tables["projects"], spec)
        required_days = {
            pair: days + len(self.forbidden_dates)
            for pair, days in _required_entry_days(tables["time_entries"]).items()
        }
        self._cluster_task_dates(tables, spec, required_days)
        self._cluster_assignment_dates(tables, spec, required_days)
        self._cluster_milestone_dates(tables, spec)
        self._cluster_time_entry_dates(tables, spec)

    def _cluster_project_dates(self, projects: Any, spec: dict[str, Any]) -> None:
        activity_start = date.fromisoformat(
            self.rules["date_windows"]["project_activity_start"]
        )
        horizon = date.fromisoformat(
            self.rules["date_windows"]["project_planning_horizon_end"]
        )
        duration_rule = self.rules["projects"]["duration_days"]
        minimum_duration = int(duration_rule["minimum"])
        latest_start = horizon - timedelta(days=minimum_duration)
        starts = self._sample_dates(
            "projects:start_date",
            len(projects),
            activity_start,
            latest_start,
            spec,
        )
        if len(starts) >= 2:
            starts[1] = starts[0]

        old_durations = [
            (date.fromisoformat(row.end_date) - date.fromisoformat(row.start_date)).days
            for row in projects.itertuples(index=False)
        ]
        ends: list[date] = []
        for start, duration in zip(starts, old_durations, strict=True):
            minimum = start + timedelta(days=minimum_duration)
            preferred = start + timedelta(days=max(minimum_duration, duration))
            end = min(horizon, max(minimum, preferred))
            ends.append(self._avoid_forbidden(end, minimum, horizon))

        projects["start_date"] = [value.isoformat() for value in starts]
        projects["end_date"] = [value.isoformat() for value in ends]
        projects["created_at"] = [
            _clamp_timestamp_on_or_before(value, start)
            for value, start in zip(projects["created_at"], starts, strict=True)
        ]

    def _cluster_task_dates(
        self,
        tables: dict[str, Any],
        spec: dict[str, Any],
        required_days: dict[tuple[int, int], int],
    ) -> None:
        projects = {
            int(row.project_id): (
                date.fromisoformat(row.start_date),
                date.fromisoformat(row.end_date),
            )
            for row in tables["projects"].itertuples(index=False)
        }
        assignments_by_task: dict[int, list[tuple[int, int]]] = {}
        for row in tables["task_resources"].itertuples(index=False):
            pair = (int(row.task_id), int(row.resource_id))
            assignments_by_task.setdefault(int(row.task_id), []).append(pair)

        tasks = tables["tasks"]
        starts: list[date] = []
        dues: list[date] = []
        completions: list[str] = []
        completed_status = self.config["business_mappings"]["task_status_dates"][
            "completed_status"
        ]
        rng = self.generator.rng_for(
            "distribution:project_management:tasks:completion_dates"
        )
        for row in tasks.itertuples(index=False):
            project_start, project_end = projects[int(row.project_id)]
            minimum_days = max(
                [
                    required_days.get(pair, 1)
                    for pair in assignments_by_task[row.task_id]
                ],
                default=1,
            )
            latest_start = project_end - timedelta(days=minimum_days - 1)
            start = self._sample_dates_from_rng(
                rng, 1, project_start, latest_start, spec
            )[0]
            old_duration = (
                date.fromisoformat(row.due_date) - date.fromisoformat(row.start_date)
            ).days
            due = min(
                project_end,
                start + timedelta(days=max(minimum_days - 1, old_duration)),
            )
            due = self._avoid_forbidden(due, start, project_end)
            if row.status == completed_status:
                completed = self._sample_dates_from_rng(
                    rng, 1, start, project_end, spec
                )[0].isoformat()
            else:
                completed = ""
            starts.append(start)
            dues.append(due)
            completions.append(completed)

        # Retain deterministic overdue and late-completion examples.
        open_position = next(
            index
            for index, status in enumerate(tasks["status"])
            if status != completed_status
        )
        open_project = projects[int(tasks.at[open_position, "project_id"])]
        overdue_due = min(
            self.settings.reference_today - timedelta(days=1), open_project[1]
        )
        if overdue_due >= open_project[0]:
            starts[open_position] = open_project[0]
            dues[open_position] = self._avoid_forbidden(
                overdue_due, open_project[0], open_project[1]
            )
        completed_position = tasks["status"].tolist().index(completed_status)
        completed_project = projects[int(tasks.at[completed_position, "project_id"])]
        starts[completed_position] = completed_project[0]
        dues[completed_position] = min(
            completed_project[1] - timedelta(days=1),
            completed_project[0] + timedelta(days=5),
        )
        completions[completed_position] = (
            dues[completed_position] + timedelta(days=1)
        ).isoformat()

        tasks["start_date"] = [value.isoformat() for value in starts]
        tasks["due_date"] = [value.isoformat() for value in dues]
        tasks["completed_date"] = completions
        tasks["created_at"] = [
            _clamp_timestamp_on_or_before(value, start)
            for value, start in zip(tasks["created_at"], starts, strict=True)
        ]

    def _cluster_assignment_dates(
        self,
        tables: dict[str, Any],
        spec: dict[str, Any],
        required_days: dict[tuple[int, int], int],
    ) -> None:
        task_windows = {
            int(row.task_id): (
                date.fromisoformat(row.start_date),
                date.fromisoformat(row.due_date),
            )
            for row in tables["tasks"].itertuples(index=False)
        }
        assignments = tables["task_resources"]
        assigned_values: list[date] = []
        released_values: list[str] = []
        rng = self.generator.rng_for(
            "distribution:project_management:task_resources:dates"
        )
        for row in assignments.itertuples(index=False):
            pair = (int(row.task_id), int(row.resource_id))
            start, due = task_windows[int(row.task_id)]
            needed = required_days.get(pair, 1)
            latest_assigned = due - timedelta(days=needed - 1)
            assigned = self._sample_dates_from_rng(
                rng, 1, start, latest_assigned, spec
            )[0]
            if row.released_at:
                minimum_release = assigned + timedelta(days=needed - 1)
                released = self._sample_dates_from_rng(
                    rng, 1, minimum_release, due, spec
                )[0].isoformat()
            else:
                released = ""
            assigned_values.append(assigned)
            released_values.append(released)

        assignments["assigned_at"] = [value.isoformat() for value in assigned_values]
        assignments["released_at"] = released_values
        assignments["created_at"] = [
            _clamp_timestamp_on_or_before(value, assigned)
            for value, assigned in zip(
                assignments["created_at"], assigned_values, strict=True
            )
        ]

    def _cluster_milestone_dates(
        self,
        tables: dict[str, Any],
        spec: dict[str, Any],
    ) -> None:
        projects = {
            int(row.project_id): (
                date.fromisoformat(row.start_date),
                date.fromisoformat(row.end_date),
            )
            for row in tables["projects"].itertuples(index=False)
        }
        milestones = tables["milestones"]
        completed_status = self.config["business_mappings"]["milestone_completion"][
            "completed_status"
        ]
        planned_values: list[date] = []
        actual_values: list[str] = []
        rng = self.generator.rng_for("distribution:project_management:milestones:dates")
        for row in milestones.itertuples(index=False):
            start, end = projects[int(row.project_id)]
            planned = self._sample_dates_from_rng(rng, 1, start, end, spec)[0]
            actual = (
                self._sample_dates_from_rng(rng, 1, start, end, spec)[0].isoformat()
                if row.status == completed_status
                else ""
            )
            planned_values.append(planned)
            actual_values.append(actual)
        milestones["planned_date"] = [value.isoformat() for value in planned_values]
        milestones["actual_date"] = actual_values
        milestones["created_at"] = [
            _clamp_timestamp_on_or_before(value, planned)
            for value, planned in zip(
                milestones["created_at"], planned_values, strict=True
            )
        ]

    def _cluster_time_entry_dates(
        self,
        tables: dict[str, Any],
        spec: dict[str, Any],
    ) -> None:
        tasks = {
            int(row.task_id): date.fromisoformat(row.due_date)
            for row in tables["tasks"].itertuples(index=False)
        }
        windows = {
            (int(row.task_id), int(row.resource_id)): (
                date.fromisoformat(row.assigned_at),
                (
                    date.fromisoformat(row.released_at)
                    if row.released_at
                    else tasks[int(row.task_id)]
                ),
            )
            for row in tables["task_resources"].itertuples(index=False)
        }
        entries = tables["time_entries"]
        result: list[date | None] = [None] * len(entries)
        grouped: dict[tuple[int, int, str], list[int]] = {}
        for position, row in enumerate(entries.itertuples(index=False)):
            key = (int(row.task_id), int(row.resource_id), str(row.work_type))
            grouped.setdefault(key, []).append(position)

        rng = self.generator.rng_for(
            "distribution:project_management:time_entries:dates"
        )
        # Draw once at a normalized scale, then map each result into its
        # assignment window. This avoids one NumPy call per work-log group.
        normalized_offsets = gaussian_mixture_offsets(
            rng,
            count=len(entries),
            day_span=1_000_000,
            component_centers=spec["component_centers"],
            component_weights=spec["component_weights"],
            std_fraction=float(spec["std_fraction"]),
        )
        for (task_id, resource_id, _), positions in grouped.items():
            start, end = windows[(task_id, resource_id)]
            day_span = (end - start).days
            candidates = [
                start
                + timedelta(
                    days=round(day_span * int(normalized_offsets[position]) / 1_000_000)
                )
                for position in positions
            ]
            unique_dates = _nearest_unique_dates(
                candidates,
                start,
                end,
                self.forbidden_dates,
            )
            for position, value in zip(positions, unique_dates, strict=True):
                result[position] = value

        entry_dates = [value for value in result if value is not None]
        if len(entry_dates) != len(entries):
            raise ValueError("Failed to assign every distributed time-entry date")
        entries["entry_date"] = [value.isoformat() for value in entry_dates]
        entries["created_at"] = [
            _timestamp_on_date(old_value, entry_date)
            for old_value, entry_date in zip(
                entries["created_at"], entry_dates, strict=True
            )
        ]

    def _sample_dates(
        self,
        stream: str,
        count: int,
        start: date,
        end: date,
        spec: dict[str, Any],
    ) -> list[date]:
        rng = self.generator.rng_for(f"distribution:project_management:{stream}")
        return self._sample_dates_from_rng(rng, count, start, end, spec)

    def _sample_dates_from_rng(
        self,
        rng: Any,
        count: int,
        start: date,
        end: date,
        spec: dict[str, Any],
    ) -> list[date]:
        if end < start:
            end = start
        values = gaussian_mixture_dates(
            rng,
            count=count,
            start=start,
            end=end,
            component_centers=spec["component_centers"],
            component_weights=spec["component_weights"],
            std_fraction=float(spec["std_fraction"]),
        )
        return [
            self._avoid_forbidden(date.fromisoformat(value), start, end)
            for value in values
        ]

    def _avoid_forbidden(self, value: date, start: date, end: date) -> date:
        if value not in self.forbidden_dates:
            return value
        for distance in range(1, (end - start).days + 2):
            for candidate in (
                value - timedelta(days=distance),
                value + timedelta(days=distance),
            ):
                if start <= candidate <= end and candidate not in self.forbidden_dates:
                    return candidate
        raise ValueError("PM date range contains no non-boundary date")


def _exact_poisson_counts(
    rng: Any,
    item_count: int,
    total_count: int,
    lam: float,
) -> list[int]:
    """Allocate an exact total with one row per item and Poisson variation."""

    if item_count <= 0 or total_count < item_count:
        raise ValueError("Poisson allocation cannot cover every PM assignment")
    weights = poisson_weights(rng, item_count=item_count, lam=lam)
    counts = 1 + rng.multinomial(total_count - item_count, weights)
    if item_count > 1 and total_count > item_count and len(set(counts.tolist())) == 1:
        donor = next((index for index, value in enumerate(counts) if value > 1), None)
        if donor is not None:
            counts[donor] -= 1
            counts[(donor + 1) % item_count] += 1
    return [int(value) for value in counts]


def _required_entry_days(entries: Any) -> dict[tuple[int, int], int]:
    """Return unique-day capacity required per assignment and work type."""

    counts: Counter[tuple[int, int, str]] = Counter(
        (int(row.task_id), int(row.resource_id), str(row.work_type))
        for row in entries.itertuples(index=False)
    )
    required: dict[tuple[int, int], int] = {}
    for (task_id, resource_id, _), count in counts.items():
        pair = (task_id, resource_id)
        required[pair] = max(required.get(pair, 1), count)
    return required


def _nearest_unique_dates(
    samples: list[date],
    start: date,
    end: date,
    forbidden: set[date],
) -> list[date]:
    """Resolve clustered samples to unique valid dates in one event group."""

    available = [
        start + timedelta(days=offset)
        for offset in range((end - start).days + 1)
        if start + timedelta(days=offset) not in forbidden
    ]
    if len(samples) > len(available):
        raise ValueError("Distributed time-entry group exceeds date capacity")
    selected: list[date] = []
    for sample in samples:
        value = min(
            available, key=lambda candidate: (abs(candidate - sample), candidate)
        )
        available.remove(value)
        selected.append(value)
    return selected


def _clamp_timestamp_on_or_before(value: Any, upper: date) -> str:
    parsed = datetime.fromisoformat(str(value))
    if parsed.date() <= upper:
        return parsed.strftime("%Y-%m-%dT%H:%M:%S")
    return datetime.combine(upper, time.min).strftime("%Y-%m-%dT%H:%M:%S")


def _timestamp_on_date(value: Any, target: date) -> str:
    parsed = datetime.fromisoformat(str(value))
    return datetime.combine(target, parsed.time()).strftime("%Y-%m-%dT%H:%M:%S")


def _is_blank(value: Any) -> bool:
    return value is None or value == "" or value != value


__all__ = ["ProjectManagementDistributionApplier"]
