from __future__ import annotations

from datetime import date
from datetime import datetime
from decimal import Decimal
import importlib.util
import inspect

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.core.imperfections import count_from_pct
from generators.project_management.distributions import (
    ProjectManagementDistributionApplier,
)
from generators.project_management.imperfections import (
    ProjectManagementImperfectionInjector,
)


pytestmark = pytest.mark.skipif(
    not all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    ),
    reason="numpy, pandas, and Faker are required",
)


@pytest.fixture(scope="module")
def distributed_and_imperfect() -> tuple[
    dict,
    dict,
    ProjectManagementImperfectionInjector,
]:
    distributed = ProjectManagementDistributionApplier.for_profile(
        "dev"
    ).generate_distributed_tables()
    snapshot = {
        table_name: table.copy(deep=True) for table_name, table in distributed.items()
    }
    injector = ProjectManagementImperfectionInjector.for_profile("dev")
    imperfect = injector.apply_to_tables(distributed)
    assert all(distributed[name].equals(snapshot[name]) for name in distributed)
    return distributed, imperfect, injector


def test_near_duplicate_time_entries_have_fresh_ids_and_permitted_variation(
    distributed_and_imperfect: tuple[
        dict,
        dict,
        ProjectManagementImperfectionInjector,
    ],
) -> None:
    distributed, imperfect, injector = distributed_and_imperfect
    entries = imperfect["time_entries"]
    base_count = len(distributed["time_entries"])
    expected = count_from_pct(base_count, float(injector.config["duplicate_pct"]))
    base_rows = entries.iloc[:base_count]
    duplicates = entries.iloc[base_count:]
    target = injector.targets["near_duplicate_time_entries"]
    keys = target["business_key_fields"]

    assert len(duplicates) == expected
    assert duplicates["entry_id"].tolist() == list(
        range(base_count + 1, base_count + expected + 1)
    )
    assert int(entries.duplicated(keys).sum()) == expected
    source_by_key = base_rows.set_index(keys, drop=False)
    for row in duplicates.itertuples(index=False):
        key = tuple(getattr(row, field) for field in keys)
        source = source_by_key.loc[key]
        assert row.hours != source["hours"]
        for field in set(entries.columns) - {"entry_id", "hours"}:
            assert getattr(row, field) == source[field]


def test_open_ended_ranges_have_independent_exact_counts_and_eligible_statuses(
    distributed_and_imperfect: tuple[
        dict,
        dict,
        ProjectManagementImperfectionInjector,
    ],
) -> None:
    _, imperfect, injector = distributed_and_imperfect
    expected_projects = count_from_pct(
        injector.generator.row_count("projects"),
        float(injector.config["null_pct"]),
    )
    expected_tasks = count_from_pct(
        injector.generator.row_count("tasks"),
        float(injector.config["null_pct"]),
    )
    projects = imperfect["projects"][imperfect["projects"]["end_date"] == ""]
    tasks = imperfect["tasks"][imperfect["tasks"]["due_date"] == ""]

    assert len(projects) == expected_projects
    assert len(tasks) == expected_tasks
    assert set(projects["status"]).issubset(
        set(injector.targets["open_ended_projects"]["eligible_statuses"])
    )
    assert set(tasks["status"]).issubset(
        set(injector.targets["open_ended_tasks"]["eligible_statuses"])
    )


def test_task_estimate_outliers_have_exact_rate_range_and_scale(
    distributed_and_imperfect: tuple[
        dict,
        dict,
        ProjectManagementImperfectionInjector,
    ],
) -> None:
    _, imperfect, injector = distributed_and_imperfect
    target = injector.targets["task_estimate_outliers"]
    minimum = Decimal(str(target["minimum_value"]))
    maximum = Decimal(str(target["maximum_value"]))
    values = imperfect["tasks"]["estimate_hours"].map(Decimal)
    outliers = values[values >= minimum]
    expected = count_from_pct(
        injector.generator.row_count("tasks"),
        float(injector.config["outlier_pct"]),
    )

    assert len(outliers) == expected
    assert outliers.le(maximum).all()
    assert outliers.map(lambda value: value.as_tuple().exponent == -2).all()


def test_every_boundary_is_present_in_each_configured_primary_target(
    distributed_and_imperfect: tuple[
        dict,
        dict,
        ProjectManagementImperfectionInjector,
    ],
) -> None:
    _, imperfect, injector = distributed_and_imperfect
    boundaries = set(injector.config["boundary_dates"])
    targets = injector.targets["coordinated_boundary_dates"]["targets"]

    for qualified in targets:
        table_name, field_name = qualified.split(".", 1)
        assert boundaries.issubset(set(imperfect[table_name][field_name]))


def test_imperfect_dates_remain_chronological_and_assignment_valid(
    distributed_and_imperfect: tuple[
        dict,
        dict,
        ProjectManagementImperfectionInjector,
    ],
) -> None:
    _, tables, _ = distributed_and_imperfect
    projects = tables["projects"].set_index("project_id")
    tasks = tables["tasks"].set_index("task_id")
    assignments = {
        (int(row.task_id), int(row.resource_id)): row
        for row in tables["task_resources"].itertuples(index=False)
    }

    for row in tables["projects"].itertuples(index=False):
        start = date.fromisoformat(row.start_date)
        assert datetime.fromisoformat(row.created_at).date() <= start
        if row.end_date:
            assert start <= date.fromisoformat(row.end_date)

    for row in tables["tasks"].itertuples(index=False):
        project = projects.loc[row.project_id]
        start = date.fromisoformat(row.start_date)
        assert date.fromisoformat(project["start_date"]) <= start
        assert datetime.fromisoformat(row.created_at).date() <= start
        if row.due_date:
            due = date.fromisoformat(row.due_date)
            assert start <= due
            if project["end_date"]:
                assert due <= date.fromisoformat(project["end_date"])
        if row.completed_date:
            completed = date.fromisoformat(row.completed_date)
            assert start <= completed

    for row in tables["time_entries"].itertuples(index=False):
        assignment = assignments[(int(row.task_id), int(row.resource_id))]
        entry = date.fromisoformat(row.entry_date)
        assert date.fromisoformat(assignment.assigned_at) <= entry
        task = tasks.loc[row.task_id]
        upper = assignment.released_at or task["due_date"]
        if upper:
            assert entry <= date.fromisoformat(upper)
        assert datetime.fromisoformat(row.created_at).date() == entry


def test_imperfect_generation_is_reproducible() -> None:
    first = ProjectManagementImperfectionInjector.for_profile(
        "dev"
    ).generate_imperfect_tables()
    second = ProjectManagementImperfectionInjector.for_profile(
        "dev"
    ).generate_imperfect_tables()
    for table_name in first:
        assert first[table_name].equals(second[table_name])


def test_imperfection_injector_rejects_other_domains() -> None:
    settings = GenerationSettings.from_config_files("finance", "dev")
    with pytest.raises(ValueError, match="only supports"):
        ProjectManagementImperfectionInjector(DeterministicGenerator(settings))


def test_imperfection_stage_does_not_read_written_datasets() -> None:
    source = inspect.getsource(
        __import__("generators.project_management.imperfections", fromlist=["*"])
    )
    assert "read_csv(" not in source
    assert "read_sql(" not in source
