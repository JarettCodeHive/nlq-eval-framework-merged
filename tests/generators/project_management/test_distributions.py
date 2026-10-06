from __future__ import annotations

from datetime import date
from decimal import Decimal
import importlib.util
import inspect

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import GenerationSettings
from generators.project_management.distributions import (
    ProjectManagementDistributionApplier,
)
from generators.project_management.generator import (
    PROJECT_MANAGEMENT_COLUMN_CONTRACTS,
)
from generators.project_management.generator import (
    ProjectManagementBaseEntityGenerator,
)
from generators.project_management.validators.generated_tables import (
    validate_project_management_generated_tables,
)


pytestmark = pytest.mark.skipif(
    not all(
        importlib.util.find_spec(package) is not None
        for package in ("numpy", "pandas", "faker")
    ),
    reason="numpy, pandas, and Faker are required",
)


@pytest.fixture(scope="module")
def base_and_distributed() -> tuple[
    dict[str, object],
    dict[str, object],
    ProjectManagementDistributionApplier,
]:
    base = ProjectManagementBaseEntityGenerator.for_profile("dev").generate_tables()
    snapshot = {name: table.copy(deep=True) for name, table in base.items()}
    applier = ProjectManagementDistributionApplier.for_profile("dev")
    distributed = applier.apply_to_tables(base)
    assert all(base[name].equals(snapshot[name]) for name in base)
    return base, distributed, applier


def test_pm_distributions_preserve_contract_and_immediate_guards(
    base_and_distributed: tuple[
        dict[str, object],
        dict[str, object],
        ProjectManagementDistributionApplier,
    ],
) -> None:
    base, distributed, applier = base_and_distributed

    assert list(distributed) == list(PROJECT_MANAGEMENT_COLUMN_CONTRACTS)
    for table_name, columns in PROJECT_MANAGEMENT_COLUMN_CONTRACTS.items():
        assert distributed[table_name].columns.tolist() == columns
        assert len(distributed[table_name]) == len(base[table_name])
    validate_project_management_generated_tables(applier.generator, distributed)


def test_pareto_budgets_preserve_nulls_and_fixed_scale(
    base_and_distributed: tuple[
        dict[str, object],
        dict[str, object],
        ProjectManagementDistributionApplier,
    ],
) -> None:
    base, distributed, applier = base_and_distributed
    before = base["projects"]["budget_amount"]
    after = distributed["projects"]["budget_amount"]
    spec = applier.settings.distributions["project_budget"]
    populated = after[after.ne("")]

    assert after.eq("").tolist() == before.eq("").tolist()
    assert not populated.equals(before[before.ne("")])
    assert (
        populated.map(Decimal)
        .between(Decimal(spec["min_amount"]), Decimal(spec["max_amount"]))
        .all()
    )
    assert populated.map(lambda value: Decimal(value).as_tuple().exponent == -2).all()


def test_poisson_frequency_is_non_uniform_and_exact(
    base_and_distributed: tuple[
        dict[str, object],
        dict[str, object],
        ProjectManagementDistributionApplier,
    ],
) -> None:
    base, distributed, _ = base_and_distributed
    entries = distributed["time_entries"]
    assignments = distributed["task_resources"]
    frequencies = entries.groupby(["task_id", "resource_id"]).size()
    declared = set(
        assignments[["task_id", "resource_id"]].itertuples(index=False, name=None)
    )

    assert len(entries) == len(base["time_entries"])
    assert int(frequencies.sum()) == len(entries)
    assert frequencies.nunique() > 1
    assert set(frequencies.index) == declared


def test_gaussian_date_pass_changes_targets_and_preserves_windows(
    base_and_distributed: tuple[
        dict[str, object],
        dict[str, object],
        ProjectManagementDistributionApplier,
    ],
) -> None:
    base, distributed, _ = base_and_distributed

    for table_name, field_name in (
        ("projects", "start_date"),
        ("projects", "end_date"),
        ("tasks", "start_date"),
        ("tasks", "due_date"),
        ("task_resources", "assigned_at"),
        ("milestones", "planned_date"),
        ("time_entries", "entry_date"),
    ):
        assert not base[table_name][field_name].equals(
            distributed[table_name][field_name]
        )

    projects = distributed["projects"].set_index("project_id")
    for row in distributed["tasks"].itertuples(index=False):
        project = projects.loc[row.project_id]
        assert date.fromisoformat(project["start_date"]) <= date.fromisoformat(
            row.start_date
        )
        assert date.fromisoformat(row.due_date) <= date.fromisoformat(
            project["end_date"]
        )


def test_distribution_pass_preserves_unrelated_attributes(
    base_and_distributed: tuple[
        dict[str, object],
        dict[str, object],
        ProjectManagementDistributionApplier,
    ],
) -> None:
    base, distributed, _ = base_and_distributed

    assert distributed["resources"].equals(base["resources"])
    for column in (
        "entry_id",
        "hours",
        "billable",
        "work_type",
        "notes",
    ):
        assert distributed["time_entries"][column].equals(base["time_entries"][column])


def test_pm_distributed_generation_is_reproducible() -> None:
    first = ProjectManagementDistributionApplier.for_profile(
        "dev"
    ).generate_distributed_tables()
    second = ProjectManagementDistributionApplier.for_profile(
        "dev"
    ).generate_distributed_tables()

    for table_name in PROJECT_MANAGEMENT_COLUMN_CONTRACTS:
        assert first[table_name].equals(second[table_name])


def test_pm_distribution_applier_rejects_other_domains() -> None:
    settings = GenerationSettings.from_config_files("crm", "dev")
    with pytest.raises(ValueError, match="only supports"):
        ProjectManagementDistributionApplier(DeterministicGenerator(settings))


def test_pm_distribution_stage_does_not_read_written_datasets() -> None:
    source = inspect.getsource(
        __import__("generators.project_management.distributions", fromlist=["*"])
    )
    assert "read_csv(" not in source
    assert "read_sql(" not in source
