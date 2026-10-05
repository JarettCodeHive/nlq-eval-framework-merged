"""Project Management configuration and schema-contract validation."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from decimal import InvalidOperation
from pathlib import Path
from typing import Any

from generators.core.base import GenerationSettings
from generators.core.base import PROJECT_ROOT
from generators.core.base import resolve_distribution_settings
from generators.core.imperfections import count_from_pct
from generators.core.schema_contract import configured_field_names
from generators.core.schema_contract import configured_foreign_keys
from generators.core.schema_contract import configured_sql_type
from generators.core.schema_contract import configured_unique_constraints
from generators.core.schema_contract import ddl_constraints
from generators.core.schema_contract import ddl_table_specs
from generators.core.schema_contract import header_table_specs
from generators.core.schema_contract import normalized_default
from generators.core.schema_contract import primary_key_fields
from generators.project_management import config as pm_config_module


REQUIRED_SECTIONS = {
    "domain",
    "release_version",
    "schema_source",
    "fixed_values",
    "release_rules",
    "table_order",
    "precision_metadata",
    "generation_notes",
    "tables",
    "relationships",
    "domain_values",
    "generation_rules",
    "business_mappings",
    "decimal_policy",
    "distributions",
    "distribution_targets",
    "imperfection_targets",
    "join_path_requirements",
    "date_rules",
    "decimal_rules",
    "currency_rules",
    "consistency_rules",
}
EXPECTED_JOIN_PATH_IDS = [f"pm_jp_{index:03d}" for index in range(1, 11)]
USD_TABLES = ("projects", "resources")


def validate_project_management_config() -> None:
    """Validate the complete PM contract before generation starts."""

    base_config = pm_config_module.load_base_config()
    config = pm_config_module.load_project_management_config()
    _validate_required_sections(config)
    _validate_explicit_references(base_config, config)
    _validate_fixed_references(base_config, config)
    _validate_schema_alignment(config)
    _validate_primary_keys(config)
    _validate_relationships(config)
    _validate_domain_values(config)
    _validate_business_mappings(config)
    _validate_generation_rules(base_config, config)
    _validate_generation_targets(base_config, config)
    _validate_join_paths(config)
    _validate_decimal_policy(config)
    _validate_usd_policy(config)
    for profile in base_config["profiles"]:
        GenerationSettings.from_configs(base_config, config, profile)


def _validate_required_sections(config: dict[str, Any]) -> None:
    """Validate required sections and basic table and field structure."""

    missing = REQUIRED_SECTIONS - set(config)
    if missing:
        raise ValueError(f"PM config is missing sections: {sorted(missing)}")
    if config.get("domain") != "project_management":
        raise ValueError("PM config domain must equal 'project_management'")

    table_order = config.get("table_order")
    tables = config.get("tables")
    if not isinstance(table_order, list) or not table_order:
        raise ValueError("PM table_order must be a non-empty list")
    if len(table_order) != len(set(table_order)):
        raise ValueError("PM table_order contains duplicates")
    if not isinstance(tables, dict) or list(tables) != table_order:
        raise ValueError("PM tables must match table_order exactly")

    for table_name in table_order:
        table = tables[table_name]
        required = {"primary_key", "role", "row_targets", "fields"}
        missing_table = required - set(table)
        if missing_table:
            raise ValueError(
                f"{table_name} is missing config keys: {sorted(missing_table)}"
            )
        fields = table["fields"]
        if not isinstance(fields, list) or not fields:
            raise ValueError(f"{table_name}.fields must be a non-empty list")
        names = [field.get("name") for field in fields]
        if any(not name for name in names) or len(names) != len(set(names)):
            raise ValueError(f"{table_name}.fields has missing or duplicate names")
        for field in fields:
            missing_field = {"name", "type", "nullable"} - set(field)
            if missing_field:
                raise ValueError(
                    f"{table_name}.{field.get('name', '<unnamed>')} is missing "
                    f"config keys: {sorted(missing_field)}"
                )


def _validate_explicit_references(
    base_config: dict[str, Any],
    config: dict[str, Any],
) -> None:
    """Resolve every explicit shared or dotted domain configuration reference."""

    for path, reference in _explicit_references(config):
        _resolve_reference(reference, base_config, config, path)


def _validate_fixed_references(
    base_config: dict[str, Any],
    config: dict[str, Any],
) -> None:
    """Validate shared fixed-value references and release-profile agreement."""

    expected = {
        "seed_source": "config/generation/base.json:seed",
        "reference_today_source": "config/generation/base.json:reference_today",
    }
    for name, reference in expected.items():
        if config["fixed_values"].get(name) != reference:
            raise ValueError(f"PM fixed_values.{name} must reference {reference}")
    if not config["fixed_values"].get("manifest_generated_at"):
        raise ValueError("PM manifest_generated_at is required")
    if config["generation_notes"].get("row_cap_source") != (
        "config/generation/base.json:max_rows_per_table"
    ):
        raise ValueError("PM row cap must reference shared base config")
    if base_config["release_profile"] != config["release_rules"]["release_profile"]:
        raise ValueError("release_profile differs between base and PM config")


def _validate_schema_alignment(config: dict[str, Any]) -> None:
    """Compare PM config with canonical DDL and signed CSV headers."""

    schema_path = PROJECT_ROOT / config["schema_source"]
    header_path = schema_path.with_name("project_management_csv_header_spec.md")
    ddl = ddl_table_specs(schema_path)
    headers = header_table_specs(header_path)
    table_order = config["table_order"]
    if list(ddl) != table_order or list(headers) != table_order:
        raise ValueError("PM table order differs across DDL, header spec, and config")

    for table_name in table_order:
        configured_fields = config["tables"][table_name]["fields"]
        configured_names = [field["name"] for field in configured_fields]
        if list(ddl[table_name]) != configured_names:
            raise ValueError(f"{table_name} fields differ between DDL and config")
        header_names = [field["name"] for field in headers[table_name]]
        if header_names != configured_names:
            raise ValueError(f"{table_name} fields differ between header and config")
        header_by_name = {field["name"]: field for field in headers[table_name]}
        for field in configured_fields:
            field_name = field["name"]
            expected_type = configured_sql_type(field)
            expected_nullable = bool(field["nullable"])
            if ddl[table_name][field_name] != {
                "type": expected_type,
                "nullable": expected_nullable,
                "default": normalized_default(field.get("default")),
            }:
                raise ValueError(
                    f"{table_name}.{field_name} differs between DDL and config"
                )
            if header_by_name[field_name] != {
                "name": field_name,
                "type": expected_type,
                "nullable": expected_nullable,
            }:
                raise ValueError(
                    f"{table_name}.{field_name} differs between header and config"
                )

    constraints = ddl_constraints(schema_path)
    configured_primary = {
        table_name: tuple(primary_key_fields(table))
        for table_name, table in config["tables"].items()
    }
    if constraints["primary_keys"] != configured_primary:
        raise ValueError("PM DDL primary keys differ from config")
    if constraints["foreign_keys"] != configured_foreign_keys(config):
        raise ValueError("PM DDL foreign keys differ from config")
    if constraints["unique_constraints"] != configured_unique_constraints(config):
        raise ValueError("PM DDL unique constraints differ from config")


def _validate_primary_keys(config: dict[str, Any]) -> None:
    """Validate scalar and composite primary-key fields and markers."""

    for table_name, table in config["tables"].items():
        fields = {field["name"]: field for field in table["fields"]}
        keys = primary_key_fields(table)
        if not keys or len(keys) != len(set(keys)):
            raise ValueError(f"{table_name} has an invalid primary key")
        for field_name in keys:
            if field_name not in fields:
                raise ValueError(f"{table_name} primary key references unknown field")
            field = fields[field_name]
            if field["nullable"]:
                raise ValueError(f"{table_name}.{field_name} primary key is nullable")
            if field.get("key") not in {"primary", "primary_foreign"}:
                raise ValueError(f"{table_name}.{field_name} lacks primary key marker")


def _validate_relationships(config: dict[str, Any]) -> None:
    """Validate physical FK coverage and semantic assignment membership."""

    physical: set[tuple[str, str, str, str]] = set()
    semantic: list[dict[str, Any]] = []
    for relationship in config["relationships"]:
        relationship_type = relationship.get("relationship_type")
        if relationship_type == "foreign_key":
            parent_table = relationship.get("parent_table")
            child_table = relationship.get("child_table")
            parent_field = relationship.get("parent_field")
            child_field = relationship.get("child_field")
            _require_field(config, parent_table, parent_field)
            _require_field(config, child_table, child_field)
            physical.add((child_table, child_field, parent_table, parent_field))
        elif relationship_type == "semantic_composite_membership":
            semantic.append(relationship)
            _validate_semantic_membership(config, relationship)
        else:
            raise ValueError(f"Unsupported PM relationship type: {relationship_type}")
    if physical != configured_foreign_keys(config):
        raise ValueError("PM physical relationships must cover configured FKs")
    if len(semantic) != 1:
        raise ValueError("PM must define one semantic assignment relationship")


def _validate_semantic_membership(
    config: dict[str, Any], relationship: dict[str, Any]
) -> None:
    """Validate the time-entry to assignment composite relationship."""

    parent_table = relationship.get("parent_table")
    child_table = relationship.get("child_table")
    parent_fields = relationship.get("parent_fields")
    child_fields = relationship.get("child_fields")
    if relationship.get("enforced_as_foreign_key") is not False:
        raise ValueError("PM semantic membership cannot be a physical FK")
    if (
        not isinstance(parent_fields, list)
        or not isinstance(child_fields, list)
        or len(parent_fields) < 2
        or len(parent_fields) != len(child_fields)
    ):
        raise ValueError("PM semantic membership requires paired composite fields")
    for field_name in parent_fields:
        _require_field(config, parent_table, field_name)
    for field_name in child_fields:
        _require_field(config, child_table, field_name)
    mapping = config["business_mappings"]["time_entry_assignment_membership"]
    if (
        parent_table != mapping.get("source_table")
        or parent_fields != mapping.get("fields")
        or child_fields != mapping.get("fields")
        or mapping.get("required_by_validator") is not True
    ):
        raise ValueError("PM semantic membership differs from business mapping")


def _validate_domain_values(config: dict[str, Any]) -> None:
    """Require every PM domain-value collection to be non-empty and unique."""

    values = config["domain_values"]
    if not isinstance(values, dict) or not values:
        raise ValueError("PM domain_values must be a non-empty object")
    for name, options in values.items():
        if not isinstance(options, list) or not options:
            raise ValueError(f"PM domain_values.{name} must be non-empty")
        if any(
            not isinstance(option, str) or not option or not option.isascii()
            for option in options
        ):
            raise ValueError(f"PM domain_values.{name} contains invalid values")
        if len(options) != len(set(options)):
            raise ValueError(f"PM domain_values.{name} contains duplicates")


def _validate_business_mappings(config: dict[str, Any]) -> None:
    """Validate PM currency, status-date, bridge, and metric mappings."""

    mappings = config["business_mappings"]
    values = config["domain_values"]
    if mappings.get("currency_code") != "USD":
        raise ValueError("PM business currency must be USD")

    status_dates = mappings.get("task_status_dates", {})
    if status_dates.get("completed_status") not in values["task_statuses"] or not all(
        status_dates.get(name) is True
        for name in ("completed_requires_date", "non_completed_requires_null_date")
    ):
        raise ValueError("PM task status-date mapping is incomplete")

    completion = mappings.get("milestone_completion", {})
    if (
        completion.get("completed_status") not in values["milestone_statuses"]
        or completion.get("rounding_mode") != "ROUND_HALF_UP"
        or completion.get("require_at_least_one_per_project") is not True
    ):
        raise ValueError("PM milestone-completion mapping is incomplete")

    bridge = mappings.get("task_resource_many_to_many", {})
    if bridge.get("bridge_table") not in config["tables"] or not all(
        bridge.get(name) is True
        for name in ("require_multi_resource_task", "require_multi_task_resource")
    ):
        raise ValueError("PM task-resource bridge mapping is incomplete")

    for name, specification in mappings.get("synthetic_names", {}).items():
        if not isinstance(specification.get("format"), str):
            raise ValueError(f"PM synthetic name mapping is invalid for {name}")


def _validate_generation_rules(
    base_config: dict[str, Any], config: dict[str, Any]
) -> None:
    """Validate PM dates, probabilities, weights, ranges, and rule coherence."""

    rules = config["generation_rules"]
    expected = {
        "date_windows",
        "projects",
        "resources",
        "tasks",
        "task_resources",
        "milestones",
        "time_entries",
    }
    if set(rules) != expected:
        raise ValueError("PM generation_rules groups differ from contract")
    dates = {
        name: _parse_date(value, f"generation_rules.date_windows.{name}")
        for name, value in rules["date_windows"].items()
    }
    required_dates = {
        "entity_created_start",
        "project_activity_start",
        "project_planning_horizon_end",
        "time_entry_activity_start",
    }
    if set(dates) != required_dates:
        raise ValueError("PM date-window fields differ from contract")
    if not (
        dates["entity_created_start"]
        <= dates["project_activity_start"]
        <= dates["time_entry_activity_start"]
        <= dates["project_planning_horizon_end"]
    ):
        raise ValueError("PM date windows are not chronological")
    date.fromisoformat(base_config["reference_today"])

    projects = rules["projects"]
    _validate_weights(
        projects["status_weights"],
        values=config["domain_values"]["project_statuses"],
        path="projects.status_weights",
    )
    _validate_weights(
        projects["priority_weights"],
        values=config["domain_values"]["project_priorities"],
        path="projects.priority_weights",
    )
    _validate_coverage(
        projects["required_status_coverage"],
        config["domain_values"]["project_statuses"],
        "projects.required_status_coverage",
    )
    _validate_integer_range(projects["duration_days"], "projects.duration_days")
    _validate_probability(
        projects["unbudgeted_probability"], "projects.unbudgeted_probability"
    )
    _validate_probability(
        projects["taskless_project_fraction"], "projects.taskless_project_fraction"
    )
    if projects.get("require_overlapping_windows") is not True:
        raise ValueError("PM projects must require overlapping windows")

    resources = rules["resources"]
    _validate_amount_range(resources["hourly_rate"], "resources.hourly_rate")
    for name in (
        "active_probability",
        "missing_rate_probability",
        "missing_department_probability",
        "missing_location_probability",
    ):
        _validate_probability(resources[name], f"resources.{name}")

    tasks = rules["tasks"]
    _validate_weights(
        tasks["status_weights"],
        config["domain_values"]["task_statuses"],
        "tasks.status_weights",
    )
    _validate_coverage(
        tasks["required_status_coverage"],
        config["domain_values"]["task_statuses"],
        "tasks.required_status_coverage",
    )
    _validate_integer_range(tasks["duration_days"], "tasks.duration_days")
    _validate_integer_range(
        tasks["completion_variance_days"],
        "tasks.completion_variance_days",
        allow_negative=True,
    )
    _validate_amount_range(tasks["estimate_hours"], "tasks.estimate_hours")
    for name in (
        "require_overlapping_windows_within_project",
        "require_overdue_examples",
        "require_completed_late_examples",
    ):
        if tasks.get(name) is not True:
            raise ValueError(f"PM tasks.{name} must be true")

    assignments = rules["task_resources"]
    minimum = assignments.get("minimum_assignments_per_task")
    maximum = assignments.get("maximum_assignments_per_task")
    if (
        not _positive_integer(minimum)
        or not _positive_integer(maximum)
        or minimum > maximum
    ):
        raise ValueError("PM task-resource assignment range is invalid")
    _validate_probability(
        assignments["release_probability"], "task_resources.release_probability"
    )
    _validate_integer_range(
        assignments["release_lag_days"], "task_resources.release_lag_days"
    )
    allocation = assignments["allocation"]
    if (
        allocation.get("algorithm") != "deterministic_integer_remainder"
        or allocation.get("scale") != config["decimal_policy"]["stored_scale"]
        or Decimal(str(allocation.get("expected_total")))
        != Decimal(config["decimal_policy"]["allocation_total"])
        or allocation.get("total_units") != 10 ** allocation["scale"] * 100
    ):
        raise ValueError("PM allocation rule differs from decimal policy")

    milestones = rules["milestones"]
    if not _positive_integer(milestones.get("minimum_per_project")):
        raise ValueError("PM milestone minimum must be positive")
    _validate_weights(
        milestones["status_weights"],
        config["domain_values"]["milestone_statuses"],
        "milestones.status_weights",
    )
    _validate_coverage(
        milestones["required_status_coverage"],
        config["domain_values"]["milestone_statuses"],
        "milestones.required_status_coverage",
    )
    _validate_integer_range(
        milestones["actual_date_variance_days"],
        "milestones.actual_date_variance_days",
        allow_negative=True,
    )

    entries = rules["time_entries"]
    _validate_unit_range(entries["hours"], "time_entries.hours")
    if entries["hours"].get("scale") != config["decimal_policy"]["stored_scale"]:
        raise ValueError("PM time-entry hours scale differs from decimal policy")
    for name in ("billable_probability", "missing_notes_probability"):
        _validate_probability(entries[name], f"time_entries.{name}")
    if entries.get("require_assignment_membership") is not True:
        raise ValueError("PM time entries must require assignment membership")


def _validate_generation_targets(
    base_config: dict[str, Any], config: dict[str, Any]
) -> None:
    """Validate profile rows, distribution targets, imperfections, and capacity."""

    profiles = set(base_config["profiles"])
    for table_name, table in config["tables"].items():
        if set(table["row_targets"]) != profiles:
            raise ValueError(f"{table_name} row targets differ from profiles")
        for profile, count in table["row_targets"].items():
            if not _positive_integer(count):
                raise ValueError(f"{table_name}.{profile} row target is invalid")
            if count > base_config["max_rows_per_table"]:
                raise ValueError(f"{table_name}.{profile} exceeds the row cap")

    resolved = resolve_distribution_settings(
        base_config["distribution_defaults"], config["distributions"]
    )
    if set(config["distribution_targets"]) != set(resolved):
        raise ValueError("PM distribution targets differ from settings")
    for name, target in config["distribution_targets"].items():
        settings_key = target.get("settings_key")
        if settings_key not in resolved:
            raise ValueError(f"{name} references unknown distribution settings")
        if target.get("distribution") != resolved[settings_key]["name"]:
            raise ValueError(f"{name} distribution algorithm differs")
        if target.get("table") is not None:
            table_name = target["table"]
            if target.get("field") is not None:
                _require_field(config, table_name, target["field"])
            for field_name in target.get("group_by_fields", []):
                _require_field(config, table_name, field_name)
        for qualified in target.get("targets", []):
            _require_qualified_field(config, qualified)

    _validate_imperfection_targets(base_config, config)
    _validate_profile_capacities(base_config, config)


def _validate_imperfection_targets(
    base_config: dict[str, Any],
    config: dict[str, Any],
) -> None:
    """Validate legal PM imperfection fields, rates, and fixed-scale ranges."""

    targets = config["imperfection_targets"]
    expected = {
        "near_duplicate_time_entries",
        "open_ended_projects",
        "open_ended_tasks",
        "task_estimate_outliers",
        "coordinated_boundary_dates",
    }
    if set(targets) != expected:
        raise ValueError("PM imperfection targets differ from contract")

    duplicate = targets["near_duplicate_time_entries"]
    for field_name in duplicate["business_key_fields"] + duplicate["variation_fields"]:
        _require_field(config, duplicate["table"], field_name)

    for name in ("open_ended_projects", "open_ended_tasks"):
        target = targets[name]
        field = _require_field(config, target["table"], target["field"])
        if not field["nullable"] or not target.get("eligible_statuses"):
            raise ValueError(f"PM {name} target must be nullable and status-limited")
        status_values = config["domain_values"][
            "project_statuses" if target["table"] == "projects" else "task_statuses"
        ]
        _validate_coverage(
            target["eligible_statuses"], status_values, name, subset=True
        )

    outlier = targets["task_estimate_outliers"]
    outlier_field = _require_field(config, outlier["table"], outlier["field"])
    if outlier_field["type"] != "decimal" or outlier.get("scale") != outlier_field.get(
        "scale"
    ):
        raise ValueError("PM task-estimate outlier target must be a matching decimal")
    clean_maximum = config["generation_rules"]["tasks"]["estimate_hours"][
        "maximum_amount"
    ]
    if outlier["minimum_value"] <= clean_maximum:
        raise ValueError("PM task-estimate outlier range overlaps clean values")
    if outlier["maximum_value"] < outlier["minimum_value"]:
        raise ValueError("PM task-estimate outlier range is invalid")

    boundary = targets["coordinated_boundary_dates"]
    for qualified in boundary["targets"] + boundary["dependent_updates"]:
        _require_qualified_field(config, qualified)
    if boundary.get("preserve_chronology") is not True:
        raise ValueError("PM boundary injection must preserve chronology")

    for name, target in targets.items():
        reference = target.get("rate_source") or target.get("values_source")
        _resolve_reference(reference, base_config, config, name)

    duplicate_pct = float(
        _resolve_reference(
            duplicate["rate_source"], base_config, config, "time-entry duplicates"
        )
    )
    for profile in base_config["profiles"]:
        rows = config["tables"]["time_entries"]["row_targets"][profile]
        if (
            rows + count_from_pct(rows, duplicate_pct)
            > base_config["max_rows_per_table"]
        ):
            raise ValueError(f"PM final time_entries exceed row cap for {profile}")


def _validate_profile_capacities(
    base_config: dict[str, Any], config: dict[str, Any]
) -> None:
    """Validate bridge, milestone, unmatched-project, and fact capacities."""

    rules = config["generation_rules"]
    for profile in base_config["profiles"]:
        rows = {
            name: table["row_targets"][profile]
            for name, table in config["tables"].items()
        }
        taskless = round(
            rows["projects"] * rules["projects"]["taskless_project_fraction"]
        )
        if taskless < 1 or rows["tasks"] < rows["projects"] - taskless:
            raise ValueError(f"PM taskless-project capacity is invalid for {profile}")
        assignments = rows["task_resources"]
        minimum = rules["task_resources"]["minimum_assignments_per_task"]
        maximum = rules["task_resources"]["maximum_assignments_per_task"]
        if not (
            assignments >= rows["tasks"] * minimum
            and assignments >= rows["resources"]
            and assignments <= rows["tasks"] * maximum
            and assignments <= rows["tasks"] * rows["resources"]
        ):
            raise ValueError(f"PM task-resource capacity is invalid for {profile}")
        if (
            rows["milestones"]
            < rows["projects"] * rules["milestones"]["minimum_per_project"]
        ):
            raise ValueError(f"PM milestone capacity is invalid for {profile}")
        if rows["time_entries"] < assignments:
            raise ValueError(f"PM time-entry capacity is invalid for {profile}")


def _validate_join_paths(config: dict[str, Any]) -> None:
    """Validate stable join IDs, configured endpoints, fields, and result rules."""

    paths = config["join_path_requirements"]
    ids = [path.get("id") for path in paths]
    if ids != EXPECTED_JOIN_PATH_IDS:
        raise ValueError(
            f"PM join path IDs differ: expected={EXPECTED_JOIN_PATH_IDS}, actual={ids}"
        )
    valid_results = {
        "non_empty",
        "non_empty_with_unmatched_parent_rows",
        "complete_membership",
    }
    for path in paths:
        path_id = path["id"]
        path_tables = path.get("tables")
        if not isinstance(path_tables, list) or len(path_tables) < 2:
            raise ValueError(f"{path_id} must contain at least two tables")
        if any(table_name not in config["tables"] for table_name in path_tables):
            raise ValueError(f"{path_id} references an unknown table")
        result = path.get("required_result")
        if result not in valid_results:
            raise ValueError(f"{path_id} has unsupported result requirement")
        if result == "non_empty_with_unmatched_parent_rows" and not path.get(
            "unmatched_condition"
        ):
            raise ValueError(f"{path_id} lacks an unmatched condition")
        _validate_condition(config, path_id, path.get("join_condition"), path_tables)
        if path.get("unmatched_condition"):
            _validate_condition(
                config, path_id, path["unmatched_condition"], path_tables
            )


def _validate_decimal_policy(config: dict[str, Any]) -> None:
    """Validate exact two-place PM storage and calculation semantics."""

    policy = config["decimal_policy"]
    release = config["precision_metadata"]
    if (
        policy.get("rounding_mode") != "ROUND_HALF_UP"
        or policy.get("calculation_order") != "calculate_first_round_once"
        or policy.get("binary_float_prohibited") is not True
        or policy.get("stored_scale") != 2
        or release.get("rounding_mode") != policy["rounding_mode"]
        or release.get("calculation_order") != policy["calculation_order"]
        or release.get("scale") != policy["stored_scale"]
    ):
        raise ValueError("PM decimal policy is inconsistent")
    expected = {
        ("projects", "budget_amount"): release["budget_precision"],
        ("resources", "hourly_rate"): release["resource_rate_precision"],
        ("tasks", "estimate_hours"): release["task_estimate_precision"],
        ("task_resources", "allocation_pct"): release["allocation_precision"],
        ("time_entries", "hours"): release["logged_hours_precision"],
    }
    for (table_name, field_name), precision in expected.items():
        field = _require_field(config, table_name, field_name)
        if (field.get("precision"), field.get("scale")) != (precision, 2):
            raise ValueError(f"{table_name}.{field_name} precision differs from policy")
    if policy.get("milestone_percentage_type") != (
        f"DECIMAL({release['metric_precision']},{release['scale']})"
    ):
        raise ValueError("PM milestone percentage type differs from policy")
    try:
        if Decimal(policy["allocation_total"]) != Decimal("100.00"):
            raise ValueError("PM allocation total must equal 100.00")
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("PM allocation total is invalid") from exc


def _validate_usd_policy(config: dict[str, Any]) -> None:
    """Require PM currency defaults and executable DDL checks to enforce USD."""

    constraints = ddl_constraints(PROJECT_ROOT / config["schema_source"])
    for table_name in USD_TABLES:
        field = _require_field(config, table_name, "currency_code")
        if field.get("default") != "USD" or field["nullable"]:
            raise ValueError(f"{table_name}.currency_code must be required USD")
        checks = constraints["checks"].get(table_name, set())
        if not any("currency_code='usd'" in check for check in checks):
            raise ValueError(f"{table_name} DDL does not enforce USD")


def _explicit_references(value: Any, path: str = "") -> list[tuple[str, str]]:
    """Collect explicit dotted references without treating strategy names as paths."""

    references: list[tuple[str, str]] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            is_reference = key.endswith("_source") and key not in {
                "schema_source",
                "synthetic_source",
            }
            is_dotted_synthetic = (
                key == "synthetic_source"
                and isinstance(child, str)
                and child.split(".", 1)[0]
                in {
                    "business_mappings",
                    "distributions",
                    "domain_values",
                    "generation_rules",
                }
            )
            if isinstance(child, str) and (is_reference or is_dotted_synthetic):
                references.append((child_path, child))
            references.extend(_explicit_references(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            references.extend(_explicit_references(child, f"{path}[{index}]"))
    return references


def _resolve_reference(
    reference: Any,
    base_config: dict[str, Any],
    config: dict[str, Any],
    context: str,
) -> Any:
    """Resolve a dotted reference against shared or assembled PM config."""

    if not isinstance(reference, str) or not reference:
        raise ValueError(f"{context} has an invalid config reference")
    if ":" in reference:
        source, dotted_path = reference.split(":", 1)
        source_name = Path(source).name
        if source_name == "base.json":
            current: Any = base_config
        elif source_name == "project_management.json":
            current = config
        else:
            raise ValueError(f"{context} references unsupported config: {source}")
    else:
        dotted_path = reference
        current = config
    for part in dotted_path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise ValueError(f"{context} references missing config value: {reference}")
        current = current[part]
    return current


def _require_field(
    config: dict[str, Any], table_name: Any, field_name: Any
) -> dict[str, Any]:
    """Return a configured PM field or raise a contextual error."""

    if table_name not in config["tables"]:
        raise ValueError(f"Unknown PM table: {table_name}")
    for field in config["tables"][table_name]["fields"]:
        if field["name"] == field_name:
            return field
    raise ValueError(f"Unknown PM field: {table_name}.{field_name}")


def _require_qualified_field(config: dict[str, Any], value: Any) -> None:
    """Validate a PM `table.field` reference."""

    if not isinstance(value, str) or value.count(".") != 1:
        raise ValueError(f"Invalid PM field reference: {value}")
    _require_field(config, *value.split("."))


def _validate_condition(
    config: dict[str, Any], path_id: str, condition: Any, path_tables: list[str]
) -> None:
    """Validate qualified fields used by one configured join condition."""

    if not isinstance(condition, str) or not condition.strip():
        raise ValueError(f"{path_id} has an invalid condition")
    references = re.findall(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\b", condition)
    if not references:
        raise ValueError(f"{path_id} condition contains no qualified fields")
    for table_name, field_name in references:
        if table_name not in path_tables:
            raise ValueError(f"{path_id} condition references table outside path")
        if field_name not in configured_field_names(config["tables"][table_name]):
            raise ValueError(f"{path_id} condition references unknown field")


def _validate_weights(mapping: Any, values: list[str], path: str) -> None:
    """Validate complete non-negative weights that sum to one."""

    if not isinstance(mapping, dict) or set(mapping) != set(values):
        raise ValueError(f"{path} must cover its configured domain values")
    if any(
        not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight < 0
        for weight in mapping.values()
    ):
        raise ValueError(f"{path} contains invalid weights")
    if abs(sum(mapping.values()) - 1.0) > 1e-9:
        raise ValueError(f"{path} weights must sum to 1")


def _validate_coverage(
    configured: Any,
    values: list[str],
    path: str,
    *,
    subset: bool = False,
) -> None:
    """Validate unique configured values against their owning domain list."""

    if not isinstance(configured, list) or not configured:
        raise ValueError(f"{path} must be a non-empty list")
    if len(configured) != len(set(configured)):
        raise ValueError(f"{path} contains duplicates")
    unknown = set(configured) - set(values)
    if unknown:
        raise ValueError(f"{path} contains unknown values: {sorted(unknown)}")
    if not subset and set(configured) != set(values):
        raise ValueError(f"{path} does not cover all configured values")


def _validate_integer_range(
    value: Any,
    path: str,
    minimum: int = 1,
    *,
    allow_negative: bool = False,
) -> None:
    """Validate an ordered inclusive integer range."""

    if not isinstance(value, dict) or not {"minimum", "maximum"} <= set(value):
        raise ValueError(f"{path} must define minimum and maximum")
    low = value["minimum"]
    high = value["maximum"]
    if (
        not isinstance(low, int)
        or isinstance(low, bool)
        or not isinstance(high, int)
        or isinstance(high, bool)
        or low > high
        or (not allow_negative and low < minimum)
    ):
        raise ValueError(f"{path} has an invalid integer range")


def _validate_amount_range(value: Any, path: str) -> None:
    """Validate a positive fixed-scale amount range."""

    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    minimum = value.get("minimum_amount")
    maximum = value.get("maximum_amount")
    scale = value.get("scale")
    if (
        not isinstance(minimum, (int, float))
        or isinstance(minimum, bool)
        or not isinstance(maximum, (int, float))
        or isinstance(maximum, bool)
        or minimum <= 0
        or maximum < minimum
        or not isinstance(scale, int)
        or isinstance(scale, bool)
        or scale < 0
    ):
        raise ValueError(f"{path} has an invalid amount range")


def _validate_unit_range(value: Any, path: str) -> None:
    """Validate a positive fixed-point range expressed as integer units."""

    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    minimum = value.get("minimum_units")
    maximum = value.get("maximum_units")
    scale = value.get("scale")
    if (
        not _positive_integer(minimum)
        or not _positive_integer(maximum)
        or maximum < minimum
        or not isinstance(scale, int)
        or isinstance(scale, bool)
        or scale < 0
    ):
        raise ValueError(f"{path} has an invalid fixed-point unit range")


def _validate_probability(value: Any, path: str) -> None:
    """Validate a probability in the inclusive range zero to one."""

    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not 0 <= value <= 1
    ):
        raise ValueError(f"{path} must be between 0 and 1")


def _parse_date(value: Any, path: str) -> date:
    """Parse one configured ISO date with a contextual error."""

    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path} must be an ISO date") from exc


def _positive_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


__all__ = ["validate_project_management_config"]
