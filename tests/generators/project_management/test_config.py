from __future__ import annotations

from copy import deepcopy

import pytest

import generators.project_management.config as config_module
import generators.project_management.validators.config as validation_module
from generators.project_management.config import load_project_management_config
from generators.project_management.config import PROJECT_MANAGEMENT_CONFIG_PATH
from generators.project_management.config import settings_for_profile
from generators.project_management.generator import (
    build_project_management_column_contracts,
)
from generators.project_management.generator import (
    PROJECT_MANAGEMENT_COLUMN_CONTRACTS,
)
from generators.project_management.validators.config import (
    validate_project_management_config,
)


EXPECTED_COLUMNS = {
    "projects": [
        "project_id",
        "project_name",
        "project_code",
        "status",
        "priority",
        "start_date",
        "end_date",
        "budget_amount",
        "currency_code",
        "created_at",
    ],
    "resources": [
        "resource_id",
        "resource_name",
        "role",
        "department",
        "location",
        "hourly_rate",
        "currency_code",
        "is_active",
        "created_at",
    ],
    "tasks": [
        "task_id",
        "project_id",
        "task_name",
        "status",
        "task_type",
        "start_date",
        "due_date",
        "completed_date",
        "estimate_hours",
        "created_at",
    ],
    "task_resources": [
        "task_id",
        "resource_id",
        "assignment_role",
        "allocation_pct",
        "assigned_at",
        "released_at",
        "created_at",
    ],
    "milestones": [
        "milestone_id",
        "project_id",
        "milestone_name",
        "milestone_type",
        "planned_date",
        "actual_date",
        "status",
        "created_at",
    ],
    "time_entries": [
        "entry_id",
        "task_id",
        "resource_id",
        "entry_date",
        "hours",
        "billable",
        "work_type",
        "notes",
        "created_at",
    ],
}


def _validate_with(
    monkeypatch: pytest.MonkeyPatch,
    config: dict[str, object],
) -> None:
    monkeypatch.setattr(
        config_module,
        "load_project_management_config",
        lambda: config,
    )
    validate_project_management_config()


def test_project_management_config_validates_before_generation() -> None:
    validate_project_management_config()


def test_project_management_loader_uses_shared_component_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {"domain": "project_management"}
    captured: dict[str, object] = {}

    def load(config_path: object, config_root: object) -> dict[str, str]:
        captured["config_path"] = config_path
        captured["config_root"] = config_root
        return expected

    monkeypatch.setattr(config_module, "load_domain_config", load)

    assert config_module.load_project_management_config() is expected
    assert captured == {
        "config_path": PROJECT_MANAGEMENT_CONFIG_PATH,
        "config_root": config_module.DEFAULT_CONFIG_ROOT,
    }


def test_project_management_settings_resolve_frozen_profiles() -> None:
    dev = settings_for_profile("dev")
    full = settings_for_profile("full")

    assert dev.table_order == (
        "projects",
        "resources",
        "tasks",
        "task_resources",
        "milestones",
        "time_entries",
    )
    assert dev.row_counts["time_entries"] == 1500
    assert full.row_counts["time_entries"] == 150000
    assert full.targets_versioned_release


def test_project_management_column_contracts_are_config_derived() -> None:
    config = load_project_management_config()

    assert PROJECT_MANAGEMENT_COLUMN_CONTRACTS == EXPECTED_COLUMNS
    assert build_project_management_column_contracts() == EXPECTED_COLUMNS
    assert {
        table_name: [field["name"] for field in config["tables"][table_name]["fields"]]
        for table_name in config["table_order"]
    } == PROJECT_MANAGEMENT_COLUMN_CONTRACTS


def test_column_contract_builder_tracks_config_changes() -> None:
    config = deepcopy(load_project_management_config())
    config["tables"]["projects"]["fields"].append(
        {
            "name": "test_only_field",
            "type": "varchar",
            "max_length": 32,
            "nullable": True,
        }
    )

    contracts = build_project_management_column_contracts(config)

    assert contracts["projects"][-1] == "test_only_field"
    assert contracts["projects"][:-1] == EXPECTED_COLUMNS["projects"]


def test_column_contract_builder_rejects_duplicate_fields() -> None:
    config = deepcopy(load_project_management_config())
    config["tables"]["projects"]["fields"].append(
        deepcopy(config["tables"]["projects"]["fields"][0])
    )

    with pytest.raises(ValueError, match="column contract contains duplicates"):
        build_project_management_column_contracts(config)


def test_validation_rejects_missing_required_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_project_management_config())
    config.pop("decimal_policy")

    with pytest.raises(ValueError, match="missing sections"):
        _validate_with(monkeypatch, config)


def test_validation_rejects_broken_dotted_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_project_management_config())
    config["business_mappings"]["synthetic_names"]["project_name"][
        "prefixes_source"
    ] = "domain_values.missing"

    with pytest.raises(ValueError, match="references missing config value"):
        _validate_with(monkeypatch, config)


def test_schema_alignment_rejects_field_drift() -> None:
    config = deepcopy(load_project_management_config())
    config["tables"]["tasks"]["fields"].pop()

    with pytest.raises(ValueError, match="tasks fields differ"):
        validation_module._validate_schema_alignment(config)


def test_validation_rejects_physical_semantic_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_project_management_config())
    config["relationships"][-1]["enforced_as_foreign_key"] = True

    with pytest.raises(ValueError, match="cannot be a physical FK"):
        _validate_with(monkeypatch, config)


def test_validation_rejects_duplicate_domain_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_project_management_config())
    config["domain_values"]["task_statuses"].append("Completed")

    with pytest.raises(ValueError, match="task_statuses contains duplicates"):
        _validate_with(monkeypatch, config)


def test_validation_rejects_weights_that_do_not_sum_to_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_project_management_config())
    config["generation_rules"]["tasks"]["status_weights"]["Completed"] = 0.1

    with pytest.raises(ValueError, match="weights must sum to 1"):
        _validate_with(monkeypatch, config)


def test_validation_rejects_non_chronological_date_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_project_management_config())
    config["generation_rules"]["date_windows"]["time_entry_activity_start"] = (
        "2028-01-01"
    )

    with pytest.raises(ValueError, match="date windows are not chronological"):
        _validate_with(monkeypatch, config)


def test_validation_rejects_row_target_above_shared_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_project_management_config())
    config["tables"]["time_entries"]["row_targets"]["full"] = 250001

    with pytest.raises(ValueError, match="exceeds the row cap"):
        _validate_with(monkeypatch, config)


def test_validation_rejects_bridge_capacity_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_project_management_config())
    config["tables"]["task_resources"]["row_targets"]["dev"] = 90

    with pytest.raises(ValueError, match="task-resource capacity"):
        _validate_with(monkeypatch, config)


def test_validation_rejects_unknown_distribution_preset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_project_management_config())
    config["distributions"]["project_budget"]["preset"] = "missing"

    with pytest.raises(ValueError, match="unknown distribution preset"):
        _validate_with(monkeypatch, config)


def test_validation_rejects_non_nullable_open_ended_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_project_management_config())
    target = config["imperfection_targets"]["open_ended_projects"]
    target["field"] = "start_date"

    with pytest.raises(ValueError, match="must be nullable and status-limited"):
        _validate_with(monkeypatch, config)


def test_validation_rejects_join_path_id_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_project_management_config())
    config["join_path_requirements"][0]["id"] = "pm_jp_999"

    with pytest.raises(ValueError, match="PM join path IDs differ"):
        _validate_with(monkeypatch, config)


def test_validation_rejects_unknown_join_condition_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_project_management_config())
    config["join_path_requirements"][0]["join_condition"] = (
        "tasks.missing_id = projects.project_id"
    )

    with pytest.raises(ValueError, match="condition references unknown field"):
        _validate_with(monkeypatch, config)


def test_validation_rejects_decimal_policy_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_project_management_config())
    config["decimal_policy"]["rounding_mode"] = "ROUND_HALF_EVEN"

    with pytest.raises(ValueError, match="decimal policy is inconsistent"):
        _validate_with(monkeypatch, config)


def test_validation_rejects_non_usd_business_currency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_project_management_config())
    config["business_mappings"]["currency_code"] = "EUR"

    with pytest.raises(ValueError, match="business currency must be USD"):
        _validate_with(monkeypatch, config)
