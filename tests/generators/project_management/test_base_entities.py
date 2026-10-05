from __future__ import annotations

from datetime import date
from datetime import datetime
from decimal import Decimal
import importlib.util
import inspect

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.core.progress import ProgressReporter
from generators.project_management.config import settings_for_profile
from generators.project_management.generator import (
    PROJECT_MANAGEMENT_COLUMN_CONTRACTS,
)
from generators.project_management.generator import (
    ProjectManagementBaseEntityGenerator,
)


def _dependencies_available() -> bool:
    return all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    )


pytestmark = pytest.mark.skipif(
    not _dependencies_available(),
    reason="numpy, pandas, and Faker are required",
)


@pytest.fixture(scope="module")
def dev_generation() -> tuple[
    ProjectManagementBaseEntityGenerator,
    dict[str, object],
]:
    generator = ProjectManagementBaseEntityGenerator.for_profile("dev")
    return generator, generator.generate_tables()


def test_generate_dev_base_tables_with_configured_shape(
    dev_generation: tuple[
        ProjectManagementBaseEntityGenerator,
        dict[str, object],
    ],
) -> None:
    generator, tables = dev_generation

    assert list(tables) == list(PROJECT_MANAGEMENT_COLUMN_CONTRACTS)
    for table_name, columns in PROJECT_MANAGEMENT_COLUMN_CONTRACTS.items():
        table = tables[table_name]
        assert table.columns.tolist() == columns
        assert len(table) == generator.generator.row_count(table_name)


def test_base_primary_keys_and_relationships_are_valid(
    dev_generation: tuple[
        ProjectManagementBaseEntityGenerator,
        dict[str, object],
    ],
) -> None:
    _, tables = dev_generation
    projects = tables["projects"]
    resources = tables["resources"]
    tasks = tables["tasks"]
    assignments = tables["task_resources"]
    milestones = tables["milestones"]
    entries = tables["time_entries"]

    for table_name, key in (
        ("projects", "project_id"),
        ("resources", "resource_id"),
        ("tasks", "task_id"),
        ("milestones", "milestone_id"),
        ("time_entries", "entry_id"),
    ):
        assert tables[table_name][key].is_unique
    assert not assignments.duplicated(["task_id", "resource_id"]).any()
    assert set(tasks["project_id"]).issubset(set(projects["project_id"]))
    assert set(milestones["project_id"]) == set(projects["project_id"])
    assert set(assignments["task_id"]) == set(tasks["task_id"])
    assert set(assignments["resource_id"]) == set(resources["resource_id"])
    assert set(entries["task_id"]).issubset(set(tasks["task_id"]))
    assert set(entries["resource_id"]).issubset(set(resources["resource_id"]))

    declared = set(
        assignments[["task_id", "resource_id"]].itertuples(index=False, name=None)
    )
    logged = set(entries[["task_id", "resource_id"]].itertuples(index=False, name=None))
    assert logged.issubset(declared)
    assert declared.issubset(logged)


def test_base_preserves_left_join_and_many_to_many_cases(
    dev_generation: tuple[
        ProjectManagementBaseEntityGenerator,
        dict[str, object],
    ],
) -> None:
    _, tables = dev_generation
    project_ids = set(tables["projects"]["project_id"])
    task_project_ids = set(tables["tasks"]["project_id"])
    assignments = tables["task_resources"]

    assert project_ids - task_project_ids
    assert assignments.groupby("task_id")["resource_id"].nunique().max() >= 2
    assert assignments.groupby("resource_id")["task_id"].nunique().max() >= 2


def test_base_dates_are_chronologically_valid(
    dev_generation: tuple[
        ProjectManagementBaseEntityGenerator,
        dict[str, object],
    ],
) -> None:
    _, tables = dev_generation
    projects = tables["projects"].set_index("project_id")
    tasks = tables["tasks"].set_index("task_id")
    assignments = tables["task_resources"]
    milestones = tables["milestones"]
    entries = tables["time_entries"]

    for row in projects.itertuples():
        start = date.fromisoformat(row.start_date)
        end = date.fromisoformat(row.end_date)
        assert datetime.fromisoformat(row.created_at).date() <= start <= end

    for row in tasks.itertuples():
        project = projects.loc[row.project_id]
        project_start = date.fromisoformat(project["start_date"])
        project_end = date.fromisoformat(project["end_date"])
        start = date.fromisoformat(row.start_date)
        due = date.fromisoformat(row.due_date)
        assert datetime.fromisoformat(row.created_at).date() <= start
        assert project_start <= start <= due <= project_end
        if row.status == "Completed":
            assert row.completed_date
            completed = date.fromisoformat(row.completed_date)
            assert start <= completed <= project_end
        else:
            assert row.completed_date == ""

    for row in assignments.itertuples(index=False):
        task = tasks.loc[row.task_id]
        assigned = date.fromisoformat(row.assigned_at)
        due = date.fromisoformat(task["due_date"])
        assert date.fromisoformat(task["start_date"]) <= assigned <= due
        assert datetime.fromisoformat(row.created_at).date() <= assigned
        if row.released_at:
            assert assigned <= date.fromisoformat(row.released_at) <= due

    for row in milestones.itertuples(index=False):
        project = projects.loc[row.project_id]
        planned = date.fromisoformat(row.planned_date)
        assert (
            date.fromisoformat(project["start_date"])
            <= planned
            <= date.fromisoformat(project["end_date"])
        )
        assert datetime.fromisoformat(row.created_at).date() <= planned
        if row.status == "Completed":
            assert row.actual_date
        else:
            assert row.actual_date == ""

    assignment_windows = {
        (int(row.task_id), int(row.resource_id)): (
            date.fromisoformat(row.assigned_at),
            date.fromisoformat(row.released_at) if row.released_at else None,
        )
        for row in assignments.itertuples(index=False)
    }
    for row in entries.itertuples(index=False):
        entry_date = date.fromisoformat(row.entry_date)
        assigned, released = assignment_windows[(row.task_id, row.resource_id)]
        assert entry_date >= assigned
        if released is not None:
            assert entry_date <= released
        assert entry_date <= date.fromisoformat(tasks.loc[row.task_id, "due_date"])
        assert datetime.fromisoformat(row.created_at).date() == entry_date


def test_base_covers_domain_values_and_date_semantics(
    dev_generation: tuple[
        ProjectManagementBaseEntityGenerator,
        dict[str, object],
    ],
) -> None:
    generator, tables = dev_generation
    values = generator.project_management_config["domain_values"]

    assert set(tables["projects"]["status"]) == set(values["project_statuses"])
    assert set(tables["projects"]["priority"]) == set(values["project_priorities"])
    assert set(tables["resources"]["role"]) == set(values["resource_roles"])
    assert set(tables["tasks"]["status"]) == set(values["task_statuses"])
    assert set(tables["tasks"]["task_type"]) == set(values["task_types"])
    assert set(tables["milestones"]["status"]) == set(values["milestone_statuses"])
    assert set(tables["time_entries"]["work_type"]) == set(values["work_types"])
    assert tables["projects"]["budget_amount"].eq("").any()
    assert tables["resources"]["hourly_rate"].eq("").any()
    assert tables["resources"]["department"].eq("").any()
    assert tables["resources"]["location"].eq("").any()
    assert tables["time_entries"]["notes"].eq("").any()

    tasks = tables["tasks"]
    overdue = tasks[
        (tasks["due_date"] < generator.settings.reference_today.isoformat())
        & (tasks["completed_date"] == "")
    ]
    completed_late = tasks[
        (tasks["completed_date"] != "") & (tasks["completed_date"] > tasks["due_date"])
    ]
    assert not overdue.empty
    assert not completed_late.empty


def test_base_decimal_values_follow_ranges_scale_and_allocation_totals(
    dev_generation: tuple[
        ProjectManagementBaseEntityGenerator,
        dict[str, object],
    ],
) -> None:
    generator, tables = dev_generation
    rules = generator.generation_rules

    populated_budgets = tables["projects"].loc[
        tables["projects"]["budget_amount"] != "", "budget_amount"
    ]
    budget_spec = generator.settings.distributions["project_budget"]
    assert (
        populated_budgets.map(Decimal)
        .between(Decimal(budget_spec["min_amount"]), Decimal(budget_spec["max_amount"]))
        .all()
    )
    populated_rates = tables["resources"].loc[
        tables["resources"]["hourly_rate"] != "", "hourly_rate"
    ]
    assert (
        populated_rates.map(Decimal)
        .between(
            Decimal(rules["resources"]["hourly_rate"]["minimum_amount"]),
            Decimal(rules["resources"]["hourly_rate"]["maximum_amount"]),
        )
        .all()
    )
    estimates = tables["tasks"]["estimate_hours"].map(Decimal)
    assert estimates.between(
        Decimal(rules["tasks"]["estimate_hours"]["minimum_amount"]),
        Decimal(rules["tasks"]["estimate_hours"]["maximum_amount"]),
    ).all()
    assert all(Decimal(value) > 0 for value in tables["time_entries"]["hours"])

    allocations = tables["task_resources"].assign(
        allocation_decimal=tables["task_resources"]["allocation_pct"].map(Decimal)
    )
    assert (
        allocations.groupby("task_id")["allocation_decimal"]
        .sum()
        .eq(Decimal("100.00"))
        .all()
    )
    for table_name, field_name in (
        ("projects", "budget_amount"),
        ("resources", "hourly_rate"),
        ("tasks", "estimate_hours"),
        ("task_resources", "allocation_pct"),
        ("time_entries", "hours"),
    ):
        populated = tables[table_name].loc[
            tables[table_name][field_name] != "", field_name
        ]
        assert all(len(value.rsplit(".", 1)[1]) == 2 for value in populated)

    assert set(tables["projects"]["currency_code"]) == {"USD"}
    assert set(tables["resources"]["currency_code"]) == {"USD"}


def test_project_management_base_generation_is_reproducible() -> None:
    settings = settings_for_profile("dev")
    first = ProjectManagementBaseEntityGenerator(
        DeterministicGenerator(settings)
    ).generate_tables()
    second = ProjectManagementBaseEntityGenerator(
        DeterministicGenerator(settings)
    ).generate_tables()

    pd = pytest.importorskip("pandas")
    for table_name in PROJECT_MANAGEMENT_COLUMN_CONTRACTS:
        pd.testing.assert_frame_equal(first[table_name], second[table_name])


def test_unrelated_named_streams_do_not_change_pm_generation() -> None:
    settings = settings_for_profile("dev")
    baseline = ProjectManagementBaseEntityGenerator(
        DeterministicGenerator(settings)
    ).generate_tables()

    isolated = DeterministicGenerator(settings)
    isolated.rng_for("project_management:test:unrelated").random(100)
    fake = isolated.faker_for("project_management:test:unrelated")
    for _ in range(100):
        fake.name()
    generated = ProjectManagementBaseEntityGenerator(isolated).generate_tables()

    pd = pytest.importorskip("pandas")
    for table_name in PROJECT_MANAGEMENT_COLUMN_CONTRACTS:
        pd.testing.assert_frame_equal(baseline[table_name], generated[table_name])


def test_base_generation_reports_table_progress() -> None:
    messages: list[str] = []
    reporter = ProgressReporter(output=messages.append)

    ProjectManagementBaseEntityGenerator.for_profile(
        "dev", progress=reporter
    ).generate_tables()

    for table_name in PROJECT_MANAGEMENT_COLUMN_CONTRACTS:
        assert any(f"{table_name}:" in message for message in messages)
    assert messages[-1] == "[progress] Base Project Management generation complete"


def test_generator_rejects_non_project_management_settings() -> None:
    with pytest.raises(ValueError, match="only supports the project_management domain"):
        ProjectManagementBaseEntityGenerator(
            DeterministicGenerator(GenerationSettings.from_config_files("sales", "dev"))
        )


def test_generation_uses_no_wall_clock_or_external_dataset_inputs() -> None:
    source = inspect.getsource(
        __import__("generators.project_management.generator", fromlist=["*"])
    )

    for forbidden_call in (
        "date.today(",
        "datetime.now(",
        "datetime.utcnow(",
        "pd.read_csv(",
        "pd.read_sql(",
    ):
        assert forbidden_call not in source
