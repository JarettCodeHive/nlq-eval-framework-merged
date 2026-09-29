"""Project Management dataframe contracts and deterministic base generation."""

from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from math import ceil
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.base import validate_column_contracts
from generators.core.distributions import format_fixed_decimal
from generators.core.fixed_point import allocate_integer_units
from generators.core.progress import ProgressReporter
from generators.core.temporal import random_date_inclusive
from generators.core.temporal import random_datetime_inclusive
from generators.project_management.config import load_project_management_config
from generators.project_management.config import settings_for_profile
from generators.project_management.validators.config import (
    validate_project_management_config,
)
from generators.project_management.validators.generated_tables import (
    validate_project_management_generated_tables,
)


def build_project_management_column_contracts(
    project_management_config: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    """Build ordered table and column contracts from PM configuration.

    The assembled schema component is the sole source of dataframe table and
    column order. Base generation and every later processing stage can therefore
    share one contract that remains aligned with the canonical DDL and signed
    CSV header specification.
    """

    config = (
        project_management_config
        if project_management_config is not None
        else load_project_management_config()
    )
    contracts = {
        table_name: [field["name"] for field in config["tables"][table_name]["fields"]]
        for table_name in config["table_order"]
    }
    validate_column_contracts(
        config["table_order"],
        config["tables"],
        contracts,
    )
    return contracts


PROJECT_MANAGEMENT_COLUMN_CONTRACTS = build_project_management_column_contracts()


class ProjectManagementBaseEntityGenerator:
    """Generate clean deterministic Project Management tables.

    The generator creates projects, resources, tasks, explicit task/resource
    assignments, milestones, and time-entry facts entirely from seeded streams
    and config-owned values. It preserves physical foreign keys, guarantees the
    task-to-resource many-to-many cardinality, and restricts every time-entry
    task/resource pair to a declared assignment.

    Controlled duplicate, NULL, outlier, and boundary-date imperfections are
    deliberately excluded from this stage.
    """

    def __init__(
        self,
        generator: DeterministicGenerator,
        progress: ProgressReporter | None = None,
    ) -> None:
        if generator.settings.domain != "project_management":
            raise ValueError(
                "ProjectManagementBaseEntityGenerator only supports the "
                "project_management domain"
            )
        validate_project_management_config()
        self.generator = generator
        self.settings = generator.settings
        self.project_management_config = load_project_management_config()
        self.generation_rules = self.project_management_config["generation_rules"]
        self.progress = progress

    @classmethod
    def for_profile(
        cls,
        profile: str,
        progress: ProgressReporter | None = None,
    ) -> "ProjectManagementBaseEntityGenerator":
        """Create a validated PM base generator for one profile."""

        return cls(
            DeterministicGenerator(settings_for_profile(profile)),
            progress=progress,
        )

    def generate_tables(self) -> dict[str, Any]:
        """Generate all clean PM tables in dependency order."""

        projects = self._generate_and_report("projects", self.generate_projects)
        resources = self._generate_and_report("resources", self.generate_resources)
        tasks = self._generate_and_report(
            "tasks", lambda: self.generate_tasks(projects)
        )
        task_resources = self._generate_and_report(
            "task_resources",
            lambda: self.generate_task_resources(tasks, resources),
        )
        milestones = self._generate_and_report(
            "milestones", lambda: self.generate_milestones(projects)
        )
        time_entries = self._generate_and_report(
            "time_entries",
            lambda: self.generate_time_entries(tasks, task_resources),
        )
        tables = {
            "projects": projects,
            "resources": resources,
            "tasks": tasks,
            "task_resources": task_resources,
            "milestones": milestones,
            "time_entries": time_entries,
        }
        self._report("Validating base Project Management tables")
        validate_project_management_generated_tables(self.generator, tables)
        self._report("Base Project Management generation complete")
        return tables

    def _generate_and_report(self, table_name: str, generate: Any) -> Any:
        """Generate one table and report its completed shape when enabled."""

        self._report(f"Generating {table_name}")
        table = generate()
        if self.progress is not None:
            self.progress.report_table(table_name, table)
        return table

    def _report(self, message: str) -> None:
        if self.progress is not None:
            self.progress.report(message)

    def generate_projects(self) -> Any:
        """Generate the clean project portfolio dimension.

        Projects receive config-owned statuses, priorities, synthetic names,
        deterministic codes, bounded schedules, optional base business NULLs
        for unapproved budgets, USD currency, and valid creation timestamps.
        Configured status coverage and overlapping project windows are seeded
        explicitly for later date-oriented questions.
        """

        pd = _require_pandas()
        count = self.generator.row_count("projects")
        rng = self.generator.rng_for("project_management:projects:attributes")
        date_rng = self.generator.rng_for("project_management:projects:dates")
        values = self.project_management_config["domain_values"]
        rules = self.generation_rules["projects"]
        names = self.project_management_config["business_mappings"]["synthetic_names"][
            "project_name"
        ]

        statuses = rng.choice(
            values["project_statuses"],
            size=count,
            p=_ordered_weights(values["project_statuses"], rules["status_weights"]),
        ).tolist()
        priorities = rng.choice(
            values["project_priorities"],
            size=count,
            p=_ordered_weights(values["project_priorities"], rules["priority_weights"]),
        ).tolist()
        _ensure_cycle_coverage(statuses, rules["required_status_coverage"])
        _ensure_cycle_coverage(priorities, values["project_priorities"])

        activity_start = _configured_date(
            self.generation_rules["date_windows"]["project_activity_start"]
        )
        horizon_end = _configured_date(
            self.generation_rules["date_windows"]["project_planning_horizon_end"]
        )
        duration = rules["duration_days"]
        latest_start = horizon_end - timedelta(days=int(duration["minimum"]))
        starts = [
            random_date_inclusive(date_rng, activity_start, latest_start)
            for _ in range(count)
        ]
        # Keep a deterministic overlap anchor regardless of random draws.
        starts[0] = activity_start
        if count >= 2:
            starts[1] = starts[0]
        forbidden_dates = _configured_boundary_dates(self.settings.imperfections)
        starts = [
            _avoid_forbidden_date(value, activity_start, latest_start, forbidden_dates)
            for value in starts
        ]
        ends = [
            min(
                horizon_end,
                start
                + timedelta(
                    days=int(
                        date_rng.integers(
                            int(duration["minimum"]),
                            int(duration["maximum"]) + 1,
                        )
                    )
                ),
            )
            for start in starts
        ]

        budget_spec = self.settings.distributions["project_budget"]
        budgets = _uniform_decimal_strings(
            rng,
            count,
            int(budget_spec["min_amount"]),
            int(budget_spec["max_amount"]),
            int(budget_spec["scale"]),
        )
        missing_budget_count = _count_from_probability(
            count, float(rules["unbudgeted_probability"])
        )
        if missing_budget_count:
            positions = rng.choice(count, size=missing_budget_count, replace=False)
            for position in positions:
                budgets[int(position)] = ""

        prefixes = values["project_name_prefixes"]
        subjects = values["project_name_subjects"]
        project_ids = self.generator.make_integer_ids(count)
        project_names = [
            names["format"].format(
                prefix=prefixes[(project_id - 1) % len(prefixes)],
                subject=subjects[(project_id - 1) % len(subjects)],
                project_id=project_id,
            )
            for project_id in project_ids
        ]
        created_start = _configured_datetime(
            self.generation_rules["date_windows"]["entity_created_start"]
        )
        created_at = [
            _format_timestamp(
                random_datetime_inclusive(
                    date_rng,
                    created_start,
                    datetime.combine(start, time.min),
                )
            )
            for start in starts
        ]
        return pd.DataFrame(
            {
                "project_id": project_ids,
                "project_name": project_names,
                "project_code": [
                    f"SYN-PM-{project_id:06d}" for project_id in project_ids
                ],
                "status": statuses,
                "priority": priorities,
                "start_date": [value.isoformat() for value in starts],
                "end_date": [value.isoformat() for value in ends],
                "budget_amount": budgets,
                "currency_code": [
                    self.project_management_config["business_mappings"]["currency_code"]
                ]
                * count,
                "created_at": created_at,
            },
            columns=PROJECT_MANAGEMENT_COLUMN_CONTRACTS["projects"],
        )

    def generate_resources(self) -> Any:
        """Generate the clean resource dimension.

        Resource names come from a seeded Faker instance. Roles and optional
        organizational attributes use config-owned vocabularies, hourly rates
        are exact fixed-scale strings, and all populated rates are USD.
        Business NULLs for unknown rates, departments, or locations are part of
        the clean contract rather than controlled imperfections.
        """

        pd = _require_pandas()
        count = self.generator.row_count("resources")
        rng = self.generator.rng_for("project_management:resources:attributes")
        date_rng = self.generator.rng_for("project_management:resources:created_at")
        fake = self.generator.faker_for("project_management:resources:faker")
        values = self.project_management_config["domain_values"]
        rules = self.generation_rules["resources"]

        roles = rng.choice(values["resource_roles"], size=count).tolist()
        _ensure_cycle_coverage(roles, values["resource_roles"])
        departments = rng.choice(values["departments"], size=count).tolist()
        locations = rng.choice(values["locations"], size=count).tolist()
        rates = _uniform_decimal_strings(
            rng,
            count,
            int(rules["hourly_rate"]["minimum_amount"]),
            int(rules["hourly_rate"]["maximum_amount"]),
            int(rules["hourly_rate"]["scale"]),
        )
        _blank_random_values(rng, rates, float(rules["missing_rate_probability"]))
        _blank_random_values(
            rng, departments, float(rules["missing_department_probability"])
        )
        _blank_random_values(
            rng, locations, float(rules["missing_location_probability"])
        )
        active_probability = float(rules["active_probability"])
        created_start = _configured_datetime(
            self.generation_rules["date_windows"]["entity_created_start"]
        )
        reference = _reference_datetime(self.settings.reference_today)
        return pd.DataFrame(
            {
                "resource_id": self.generator.make_integer_ids(count),
                "resource_name": [fake.name() for _ in range(count)],
                "role": roles,
                "department": departments,
                "location": locations,
                "hourly_rate": rates,
                "currency_code": [
                    self.project_management_config["business_mappings"]["currency_code"]
                ]
                * count,
                "is_active": rng.choice(
                    [True, False],
                    size=count,
                    p=[active_probability, 1 - active_probability],
                ).tolist(),
                "created_at": [
                    _format_timestamp(
                        random_datetime_inclusive(date_rng, created_start, reference)
                    )
                    for _ in range(count)
                ],
            },
            columns=PROJECT_MANAGEMENT_COLUMN_CONTRACTS["resources"],
        )

    def generate_tasks(self, projects: Any) -> Any:
        """Generate project tasks while preserving the configured LEFT JOIN gap.

        Every task references a valid non-taskless project and falls within its
        project window. All task statuses and types are represented. Completed
        tasks receive completion dates, non-completed tasks keep that field
        blank, and deterministic examples exercise overdue and completed-late
        semantics without introducing controlled NULL imperfections.
        """

        pd = _require_pandas()
        count = self.generator.row_count("tasks")
        rng = self.generator.rng_for("project_management:tasks:attributes")
        date_rng = self.generator.rng_for("project_management:tasks:dates")
        values = self.project_management_config["domain_values"]
        rules = self.generation_rules["tasks"]
        project_rules = self.generation_rules["projects"]
        project_rows = list(projects.itertuples(index=False))
        taskless_count = max(
            1,
            round(
                len(project_rows) * float(project_rules["taskless_project_fraction"])
            ),
        )
        eligible_projects = project_rows[:-taskless_count]
        if not eligible_projects:
            raise ValueError("PM task generation requires a task-bearing project")

        project_positions = [
            index % len(eligible_projects) for index in range(len(eligible_projects))
        ]
        if count > len(project_positions):
            project_positions.extend(
                int(value)
                for value in rng.integers(
                    0, len(eligible_projects), size=count - len(project_positions)
                )
            )
        selected_projects = [eligible_projects[index] for index in project_positions]

        statuses = rng.choice(
            values["task_statuses"],
            size=count,
            p=_ordered_weights(values["task_statuses"], rules["status_weights"]),
        ).tolist()
        _ensure_cycle_coverage(statuses, rules["required_status_coverage"])
        task_types = rng.choice(values["task_types"], size=count).tolist()
        _ensure_cycle_coverage(task_types, values["task_types"])

        starts: list[date] = []
        dues: list[date] = []
        completions: list[str] = []
        created_values: list[str] = []
        entity_created_start = _configured_datetime(
            self.generation_rules["date_windows"]["entity_created_start"]
        )
        for project, status in zip(selected_projects, statuses, strict=True):
            project_start = date.fromisoformat(project.start_date)
            project_end = date.fromisoformat(project.end_date)
            maximum_duration = min(
                int(rules["duration_days"]["maximum"]),
                max(1, (project_end - project_start).days),
            )
            start = random_date_inclusive(date_rng, project_start, project_end)
            remaining = max(0, (project_end - start).days)
            minimum_duration = min(int(rules["duration_days"]["minimum"]), remaining)
            duration_days = int(
                date_rng.integers(
                    minimum_duration, min(maximum_duration, remaining) + 1
                )
            )
            due = start + timedelta(days=duration_days)
            completed = ""
            if (
                status
                == self.project_management_config["business_mappings"][
                    "task_status_dates"
                ]["completed_status"]
            ):
                variance = rules["completion_variance_days"]
                offset = int(
                    date_rng.integers(
                        int(variance["minimum"]), int(variance["maximum"]) + 1
                    )
                )
                completed_date = min(
                    project_end, max(start, due + timedelta(days=offset))
                )
                completed = completed_date.isoformat()
            starts.append(start)
            dues.append(due)
            completions.append(completed)
            created_values.append(
                _format_timestamp(
                    random_datetime_inclusive(
                        date_rng,
                        entity_created_start,
                        datetime.combine(start, time.min),
                    )
                )
            )

        # Deterministic semantic anchors: one overdue open task and one late task.
        open_position = next(
            index for index, status in enumerate(statuses) if status != "Completed"
        )
        overdue_project = selected_projects[open_position]
        overdue_start = date.fromisoformat(overdue_project.start_date)
        overdue_end = date.fromisoformat(overdue_project.end_date)
        overdue_due = min(
            self.settings.reference_today - timedelta(days=1), overdue_end
        )
        if overdue_due >= overdue_start:
            starts[open_position] = overdue_start
            dues[open_position] = overdue_due
            created_values[open_position] = overdue_project.created_at
        completed_position = statuses.index("Completed")
        completed_project = selected_projects[completed_position]
        completed_start = date.fromisoformat(completed_project.start_date)
        completed_project_end = date.fromisoformat(completed_project.end_date)
        completed_due = min(
            completed_project_end - timedelta(days=1),
            completed_start + timedelta(days=int(rules["duration_days"]["minimum"])),
        )
        starts[completed_position] = completed_start
        dues[completed_position] = completed_due
        completions[completed_position] = (
            completed_due + timedelta(days=1)
        ).isoformat()
        created_values[completed_position] = completed_project.created_at

        forbidden_dates = _configured_boundary_dates(self.settings.imperfections)
        for position, project in enumerate(selected_projects):
            project_start = date.fromisoformat(project.start_date)
            project_end = date.fromisoformat(project.end_date)
            starts[position] = _avoid_forbidden_date(
                starts[position], project_start, dues[position], forbidden_dates
            )
            dues[position] = _avoid_forbidden_date(
                dues[position], starts[position], project_end, forbidden_dates
            )

        estimates = _uniform_decimal_strings(
            rng,
            count,
            int(rules["estimate_hours"]["minimum_amount"]),
            int(rules["estimate_hours"]["maximum_amount"]),
            int(rules["estimate_hours"]["scale"]),
        )
        task_ids = self.generator.make_integer_ids(count)
        names = self.project_management_config["business_mappings"]["synthetic_names"][
            "task_name"
        ]
        actions = values["task_name_actions"]
        objects = values["task_name_objects"]
        return pd.DataFrame(
            {
                "task_id": task_ids,
                "project_id": [
                    int(project.project_id) for project in selected_projects
                ],
                "task_name": [
                    names["format"].format(
                        action=actions[(task_id - 1) % len(actions)],
                        object=objects[(task_id - 1) % len(objects)],
                        task_id=task_id,
                    )
                    for task_id in task_ids
                ],
                "status": statuses,
                "task_type": task_types,
                "start_date": [value.isoformat() for value in starts],
                "due_date": [value.isoformat() for value in dues],
                "completed_date": completions,
                "estimate_hours": estimates,
                "created_at": created_values,
            },
            columns=PROJECT_MANAGEMENT_COLUMN_CONTRACTS["tasks"],
        )

    def generate_task_resources(self, tasks: Any, resources: Any) -> Any:
        """Generate the explicit Task-to-Resource many-to-many bridge.

        Unique assignment pairs cover every task and every resource, guarantee
        both directions of many-to-many cardinality, and obey the configured
        per-task maximum. Allocation percentages are split as integer
        hundredths so each task sums exactly to 100.00 without float rounding.
        Assignment and release dates remain inside their task windows.
        """

        pd = _require_pandas()
        count = self.generator.row_count("task_resources")
        pair_rng = self.generator.rng_for("project_management:task_resources:pairs")
        rng = self.generator.rng_for("project_management:task_resources:attributes")
        date_rng = self.generator.rng_for("project_management:task_resources:dates")
        rules = self.generation_rules["task_resources"]
        task_ids = [int(value) for value in tasks["task_id"]]
        resource_ids = [int(value) for value in resources["resource_id"]]
        pairs = _unique_assignment_pairs(
            pair_rng,
            task_ids,
            resource_ids,
            count,
            int(rules["maximum_assignments_per_task"]),
        )
        task_windows = {
            int(row.task_id): (
                date.fromisoformat(row.start_date),
                date.fromisoformat(row.due_date),
            )
            for row in tasks.itertuples(index=False)
        }

        assigned_dates: list[date] = []
        released_dates: list[str] = []
        created_values: list[str] = []
        created_start = _configured_datetime(
            self.generation_rules["date_windows"]["entity_created_start"]
        )
        release_probability = float(rules["release_probability"])
        for task_id, _ in pairs:
            task_start, task_end = task_windows[task_id]
            assigned = random_date_inclusive(date_rng, task_start, task_end)
            released = ""
            if float(rng.random()) < release_probability and assigned < task_end:
                maximum_lag = min(
                    int(rules["release_lag_days"]["maximum"]),
                    (task_end - assigned).days,
                )
                minimum_lag = min(
                    int(rules["release_lag_days"]["minimum"]), maximum_lag
                )
                released = (
                    assigned
                    + timedelta(
                        days=int(date_rng.integers(minimum_lag, maximum_lag + 1))
                    )
                ).isoformat()
            assigned_dates.append(assigned)
            released_dates.append(released)
            created_values.append(
                _format_timestamp(
                    random_datetime_inclusive(
                        date_rng,
                        created_start,
                        datetime.combine(assigned, time.min),
                    )
                )
            )

        assignments_by_task: dict[int, list[int]] = {}
        for position, (task_id, _) in enumerate(pairs):
            assignments_by_task.setdefault(task_id, []).append(position)
        allocation_units = [0] * count
        total_units = int(rules["allocation"]["total_units"])
        scale = int(rules["allocation"]["scale"])
        for positions in assignments_by_task.values():
            units = allocate_integer_units(total_units, len(positions))
            for position, value in zip(positions, units, strict=True):
                allocation_units[position] = value

        assignment_roles = self.project_management_config["domain_values"][
            "assignment_roles"
        ]
        roles = rng.choice(assignment_roles, size=count).tolist()
        _ensure_cycle_coverage(roles, assignment_roles)
        return pd.DataFrame(
            {
                "task_id": [task_id for task_id, _ in pairs],
                "resource_id": [resource_id for _, resource_id in pairs],
                "assignment_role": roles,
                "allocation_pct": [
                    format_fixed_decimal(value, scale) for value in allocation_units
                ],
                "assigned_at": [value.isoformat() for value in assigned_dates],
                "released_at": released_dates,
                "created_at": created_values,
            },
            columns=PROJECT_MANAGEMENT_COLUMN_CONTRACTS["task_resources"],
        )

    def generate_milestones(self, projects: Any) -> Any:
        """Generate project milestones with status-consistent actual dates.

        Every project receives at least one milestone before remaining rows are
        assigned. Planned and actual dates stay within the parent project;
        completed milestones receive actual dates while all other statuses keep
        them blank for completion-percentage questions.
        """

        pd = _require_pandas()
        count = self.generator.row_count("milestones")
        rng = self.generator.rng_for("project_management:milestones:attributes")
        date_rng = self.generator.rng_for("project_management:milestones:dates")
        values = self.project_management_config["domain_values"]
        rules = self.generation_rules["milestones"]
        project_rows = list(projects.itertuples(index=False))
        positions = list(range(len(project_rows)))
        positions.extend(
            int(value)
            for value in rng.integers(0, len(project_rows), size=count - len(positions))
        )
        selected_projects = [project_rows[position] for position in positions]
        statuses = rng.choice(
            values["milestone_statuses"],
            size=count,
            p=_ordered_weights(values["milestone_statuses"], rules["status_weights"]),
        ).tolist()
        _ensure_cycle_coverage(statuses, rules["required_status_coverage"])
        milestone_types = rng.choice(values["milestone_types"], size=count).tolist()
        _ensure_cycle_coverage(milestone_types, values["milestone_types"])
        completed_status = self.project_management_config["business_mappings"][
            "milestone_completion"
        ]["completed_status"]
        variance = rules["actual_date_variance_days"]
        planned_values: list[str] = []
        actual_values: list[str] = []
        created_values: list[str] = []
        created_start = _configured_datetime(
            self.generation_rules["date_windows"]["entity_created_start"]
        )
        for project, status in zip(selected_projects, statuses, strict=True):
            project_start = date.fromisoformat(project.start_date)
            project_end = date.fromisoformat(project.end_date)
            planned = random_date_inclusive(date_rng, project_start, project_end)
            planned = _avoid_forbidden_date(
                planned,
                project_start,
                project_end,
                _configured_boundary_dates(self.settings.imperfections),
            )
            actual = ""
            if status == completed_status:
                offset = int(
                    date_rng.integers(
                        int(variance["minimum"]), int(variance["maximum"]) + 1
                    )
                )
                actual = min(
                    project_end, max(project_start, planned + timedelta(days=offset))
                ).isoformat()
            planned_values.append(planned.isoformat())
            actual_values.append(actual)
            created_values.append(
                _format_timestamp(
                    random_datetime_inclusive(
                        date_rng,
                        created_start,
                        datetime.combine(planned, time.min),
                    )
                )
            )

        milestone_ids = self.generator.make_integer_ids(count)
        names = self.project_management_config["business_mappings"]["synthetic_names"][
            "milestone_name"
        ]
        prefixes = values["milestone_name_prefixes"]
        return pd.DataFrame(
            {
                "milestone_id": milestone_ids,
                "project_id": [
                    int(project.project_id) for project in selected_projects
                ],
                "milestone_name": [
                    names["format"].format(
                        prefix=prefixes[(milestone_id - 1) % len(prefixes)],
                        milestone_id=milestone_id,
                    )
                    for milestone_id in milestone_ids
                ],
                "milestone_type": milestone_types,
                "planned_date": planned_values,
                "actual_date": actual_values,
                "status": statuses,
                "created_at": created_values,
            },
            columns=PROJECT_MANAGEMENT_COLUMN_CONTRACTS["milestones"],
        )

    def generate_time_entries(self, tasks: Any, task_resources: Any) -> Any:
        """Generate work-log facts from declared task/resource assignments.

        Each assignment is represented at least once before remaining rows are
        sampled. Entry dates stay within assignment and task windows, hours are
        positive fixed-scale strings, and optional notes use config-owned text.
        The function never creates an undeclared task/resource combination.
        """

        pd = _require_pandas()
        count = self.generator.row_count("time_entries")
        pair_rng = self.generator.rng_for("project_management:time_entries:pairs")
        rng = self.generator.rng_for("project_management:time_entries:attributes")
        date_rng = self.generator.rng_for("project_management:time_entries:dates")
        rules = self.generation_rules["time_entries"]
        assignment_rows = list(task_resources.itertuples(index=False))
        work_types = self.project_management_config["domain_values"]["work_types"]
        forbidden_dates = _configured_boundary_dates(self.settings.imperfections)
        task_end = {
            int(row.task_id): date.fromisoformat(row.due_date)
            for row in tasks.itertuples(index=False)
        }
        assignment_windows = [
            (
                date.fromisoformat(row.assigned_at),
                (
                    date.fromisoformat(row.released_at)
                    if row.released_at
                    else task_end[int(row.task_id)]
                ),
            )
            for row in assignment_rows
        ]
        assignment_capacities = [
            _clean_date_count(start, end, forbidden_dates) * len(work_types)
            for start, end in assignment_windows
        ]
        assignment_positions = _event_assignment_positions(
            pair_rng,
            assignment_capacities,
            count,
        )
        selected = [assignment_rows[position] for position in assignment_positions]
        positions_by_assignment: dict[int, list[int]] = {}
        for output_position, assignment_position in enumerate(assignment_positions):
            positions_by_assignment.setdefault(assignment_position, []).append(
                output_position
            )
        event_slots = [0] * count
        for assignment_position, output_positions in positions_by_assignment.items():
            slots = date_rng.choice(
                assignment_capacities[assignment_position],
                size=len(output_positions),
                replace=False,
            )
            for output_position, slot in zip(output_positions, slots, strict=True):
                event_slots[output_position] = int(slot)

        entry_dates: list[date] = []
        selected_work_types: list[str] = []
        created_values: list[str] = []
        for assignment, slot in zip(selected, event_slots, strict=True):
            day_offset, work_type_position = divmod(slot, len(work_types))
            assignment_position = assignment_positions[len(entry_dates)]
            start, end = assignment_windows[assignment_position]
            entry_date = _clean_date_at_offset(
                start,
                end,
                day_offset,
                forbidden_dates,
            )
            entry_dates.append(entry_date)
            selected_work_types.append(work_types[work_type_position])
            created_values.append(
                _format_timestamp(
                    datetime.combine(entry_date, time.min)
                    + timedelta(seconds=int(date_rng.integers(0, 86400)))
                )
            )

        hours = rules["hours"]
        scale = int(hours["scale"])
        hour_units = rng.integers(
            int(hours["minimum_units"]),
            int(hours["maximum_units"]) + 1,
            size=count,
        )
        notes = rng.choice(
            self.project_management_config["domain_values"][
                "time_entry_note_templates"
            ],
            size=count,
        ).tolist()
        _blank_random_values(rng, notes, float(rules["missing_notes_probability"]))
        billable_probability = float(rules["billable_probability"])
        return pd.DataFrame(
            {
                "entry_id": self.generator.make_integer_ids(count),
                "task_id": [int(row.task_id) for row in selected],
                "resource_id": [int(row.resource_id) for row in selected],
                "entry_date": [value.isoformat() for value in entry_dates],
                "hours": [
                    format_fixed_decimal(int(value), scale) for value in hour_units
                ],
                "billable": rng.choice(
                    [True, False],
                    size=count,
                    p=[billable_probability, 1 - billable_probability],
                ).tolist(),
                "work_type": selected_work_types,
                "notes": notes,
                "created_at": created_values,
            },
            columns=PROJECT_MANAGEMENT_COLUMN_CONTRACTS["time_entries"],
        )


def _unique_assignment_pairs(
    rng: Any,
    task_ids: list[int],
    resource_ids: list[int],
    count: int,
    maximum_per_task: int,
) -> list[tuple[int, int]]:
    """Build unique assignment pairs with complete and many-to-many coverage."""

    if not task_ids or not resource_ids:
        raise ValueError("PM assignments require tasks and resources")
    capacity = len(task_ids) * min(len(resource_ids), maximum_per_task)
    if count > capacity:
        raise ValueError("PM assignment target exceeds configured pair capacity")
    if count < max(len(task_ids), len(resource_ids)):
        raise ValueError("PM assignment target cannot cover every task and resource")

    pairs: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    counts: dict[int, int] = {task_id: 0 for task_id in task_ids}

    def append(task_id: int, resource_id: int) -> None:
        pair = (task_id, resource_id)
        if pair not in seen and counts[task_id] < maximum_per_task:
            seen.add(pair)
            pairs.append(pair)
            counts[task_id] += 1

    for index, task_id in enumerate(task_ids):
        append(task_id, resource_ids[index % len(resource_ids)])
    for index, resource_id in enumerate(resource_ids):
        append(task_ids[index % len(task_ids)], resource_id)
    if len(resource_ids) > 1:
        append(task_ids[0], resource_ids[1])
    if len(task_ids) > 1:
        append(task_ids[1], resource_ids[0])

    while len(pairs) < count:
        remaining = count - len(pairs)
        batch_size = max(remaining * 2, 64)
        sampled_tasks = rng.choice(task_ids, size=batch_size)
        sampled_resources = rng.choice(resource_ids, size=batch_size)
        for task_id, resource_id in zip(sampled_tasks, sampled_resources, strict=True):
            append(int(task_id), int(resource_id))
            if len(pairs) == count:
                break
    return pairs


def _event_assignment_positions(
    rng: Any,
    capacities: list[int],
    count: int,
) -> list[int]:
    """Allocate event rows without exceeding unique business-key capacity."""

    if not capacities or any(capacity <= 0 for capacity in capacities):
        raise ValueError("PM time-entry assignments must have positive capacity")
    if count < len(capacities):
        raise ValueError("PM time-entry target must cover every assignment")
    if count > sum(capacities):
        raise ValueError("PM time-entry target exceeds unique business-key capacity")

    positions = list(range(len(capacities)))
    used = [1] * len(capacities)
    while len(positions) < count:
        remaining = count - len(positions)
        candidates = [
            position
            for position, capacity in enumerate(capacities)
            if used[position] < capacity
        ]
        if not candidates:
            raise ValueError("PM time-entry capacity was exhausted")
        batch = rng.choice(candidates, size=min(remaining, len(candidates)))
        for value in batch:
            position = int(value)
            if used[position] < capacities[position]:
                positions.append(position)
                used[position] += 1
                if len(positions) == count:
                    break
    return positions


def _uniform_decimal_strings(
    rng: Any,
    count: int,
    minimum: int,
    maximum: int,
    scale: int,
) -> list[str]:
    multiplier = 10**scale
    values = rng.integers(
        minimum * multiplier,
        (maximum * multiplier) + 1,
        size=count,
    )
    return [format_fixed_decimal(int(value), scale) for value in values]


def _blank_random_values(rng: Any, values: list[Any], probability: float) -> None:
    count = _count_from_probability(len(values), probability)
    if count:
        positions = rng.choice(len(values), size=count, replace=False)
        for position in positions:
            values[int(position)] = ""


def _count_from_probability(total: int, probability: float) -> int:
    if total <= 0 or probability <= 0:
        return 0
    return min(total, max(1, ceil(total * probability)))


def _configured_boundary_dates(imperfections: dict[str, Any]) -> set[date]:
    return {date.fromisoformat(value) for value in imperfections["boundary_dates"]}


def _avoid_forbidden_date(
    value: date,
    minimum: date,
    maximum: date,
    forbidden: set[date],
) -> date:
    """Move a reserved boundary date within its valid clean-stage window."""

    if value not in forbidden:
        return value
    forward = value + timedelta(days=1)
    if forward <= maximum and forward not in forbidden:
        return forward
    backward = value - timedelta(days=1)
    if backward >= minimum and backward not in forbidden:
        return backward
    raise ValueError("PM clean date window contains only reserved boundary dates")


def _clean_date_count(start: date, end: date, forbidden: set[date]) -> int:
    """Count inclusive dates after removing reserved clean-stage boundaries."""

    if end < start:
        raise ValueError("PM assignment end precedes its start")
    count = (end - start).days + 1
    count -= sum(start <= value <= end for value in forbidden)
    if count <= 0:
        raise ValueError("PM assignment has no non-boundary time-entry dates")
    return count


def _clean_date_at_offset(
    start: date,
    end: date,
    offset: int,
    forbidden: set[date],
) -> date:
    """Resolve a zero-based offset while skipping reserved boundary dates."""

    count = _clean_date_count(start, end, forbidden)
    if offset < 0 or offset >= count:
        raise ValueError("PM clean date offset is outside the assignment window")
    current = start
    remaining = offset
    for boundary in sorted(value for value in forbidden if start <= value <= end):
        allowed_before = (boundary - current).days
        if remaining < allowed_before:
            return current + timedelta(days=remaining)
        remaining -= allowed_before
        current = boundary + timedelta(days=1)
    return current + timedelta(days=remaining)


def _configured_date(value: str) -> date:
    return date.fromisoformat(value)


def _configured_datetime(value: str) -> datetime:
    return datetime.combine(_configured_date(value), time.min)


def _reference_datetime(value: date) -> datetime:
    return datetime.combine(value, time.max.replace(microsecond=0))


def _format_timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S")


def _ordered_weights(values: list[str], weights: dict[str, Any]) -> list[float]:
    return [float(weights[value]) for value in values]


def _ensure_cycle_coverage(values: list[Any], required: list[Any]) -> None:
    for position, value in enumerate(required[: len(values)]):
        values[position] = value


def _require_pandas() -> Any:
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError(
            "Missing required dependency 'pandas'. Install requirements.txt."
        ) from exc
    return pd


__all__ = [
    "PROJECT_MANAGEMENT_COLUMN_CONTRACTS",
    "ProjectManagementBaseEntityGenerator",
    "build_project_management_column_contracts",
]
