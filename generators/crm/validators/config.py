"""CRM config, schema, DDL, and generation-rule validation."""

from __future__ import annotations

import re
from datetime import date
from datetime import timedelta
from decimal import Decimal
from decimal import InvalidOperation
from pathlib import Path
from typing import Any

from generators.core.base import GenerationSettings
from generators.core.base import PROJECT_ROOT
from generators.core.base import resolve_distribution_settings
from generators.crm import config as crm_config_module


def validate_crm_config() -> None:
    """Validate CRM config and DDL alignment before generation code runs."""

    base_config = crm_config_module.load_base_config()
    crm_config = crm_config_module.load_crm_config()
    _validate_required_sections(crm_config)
    _validate_fixed_references(base_config, crm_config)
    _validate_schema_alignment(crm_config)
    _validate_primary_keys(crm_config)
    _validate_relationships(crm_config)
    _validate_domain_values(crm_config)
    _validate_business_mappings(crm_config)
    _validate_generation_rules(base_config, crm_config)
    _validate_generation_targets(base_config, crm_config)
    _validate_join_path_requirements(crm_config)
    for profile in base_config["profiles"]:
        GenerationSettings.from_configs(base_config, crm_config, profile)


def _validate_required_sections(crm_config: dict[str, Any]) -> None:
    """Check required sections and basic table and field structure."""

    required = {
        "domain_values",
        "generation_rules",
        "business_mappings",
        "distributions",
        "distribution_targets",
        "imperfection_targets",
        "relationships",
        "join_path_requirements",
        "consistency_rules",
    }
    missing = required - set(crm_config)
    if missing:
        raise ValueError(f"CRM config is missing sections: {sorted(missing)}")

    table_order = crm_config.get("table_order")
    tables = crm_config.get("tables")
    if not isinstance(table_order, list) or not table_order:
        raise ValueError("CRM table_order must be a non-empty list")
    if len(table_order) != len(set(table_order)):
        raise ValueError("CRM table_order contains duplicates")
    if not isinstance(tables, dict) or set(table_order) != set(tables):
        raise ValueError(
            "CRM tables must match table_order exactly: "
            f"table_order={table_order}, tables={sorted(tables or {})}"
        )
    for table_name in table_order:
        table = tables[table_name]
        missing_table_keys = {"primary_key", "role", "row_targets", "fields"} - set(
            table
        )
        if missing_table_keys:
            raise ValueError(
                f"{table_name} is missing config keys: {sorted(missing_table_keys)}"
            )
        fields = table["fields"]
        if not isinstance(fields, list) or not fields:
            raise ValueError(f"{table_name}.fields must be a non-empty list")
        field_names = [field.get("name") for field in fields]
        if any(not name for name in field_names) or len(field_names) != len(
            set(field_names)
        ):
            raise ValueError(f"{table_name}.fields has missing or duplicate names")
        for field in fields:
            missing_field_keys = {"name", "type", "nullable"} - set(field)
            if missing_field_keys:
                raise ValueError(
                    f"{table_name}.{field.get('name', '<unnamed>')} is missing "
                    f"config keys: {sorted(missing_field_keys)}"
                )


def _validate_fixed_references(
    base_config: dict[str, Any],
    crm_config: dict[str, Any],
) -> None:
    """Check shared config references and release-profile consistency."""

    fixed_values = crm_config["fixed_values"]
    for source_name in ("seed_source", "reference_today_source"):
        if source_name not in fixed_values:
            raise ValueError(f"CRM fixed_values is missing {source_name}")
        _resolve_config_reference(
            fixed_values[source_name], base_config, crm_config, source_name
        )
    if "manifest_generated_at" not in fixed_values:
        raise ValueError("CRM fixed_values is missing manifest_generated_at")
    if base_config["release_profile"] != crm_config["release_rules"]["release_profile"]:
        raise ValueError("release_profile differs between base and CRM config")


def _validate_schema_alignment(crm_config: dict[str, Any]) -> None:
    """Ensure the configured schema exactly matches the canonical CRM DDL."""

    schema_source = PROJECT_ROOT / crm_config["schema_source"]
    ddl_specs = _ddl_table_specs(schema_source)
    ddl_tables = {
        table_name: list(field_specs) for table_name, field_specs in ddl_specs.items()
    }
    configured_tables = crm_config["tables"]
    table_order = list(crm_config["table_order"])

    if list(ddl_tables) != table_order:
        raise ValueError(
            "DDL table order does not match CRM table_order: "
            f"ddl={list(ddl_tables)}, config={table_order}"
        )
    for table_name, ddl_fields in ddl_tables.items():
        if table_name not in configured_tables:
            raise ValueError(f"DDL table is missing from CRM config: {table_name}")
        config_fields = [
            field["name"] for field in configured_tables[table_name]["fields"]
        ]
        if ddl_fields != config_fields:
            raise ValueError(
                f"{table_name} fields differ between DDL and config: "
                f"ddl={ddl_fields}, config={config_fields}"
            )
        config_by_name = {
            field["name"]: field for field in configured_tables[table_name]["fields"]
        }
        for field_name, ddl_field in ddl_specs[table_name].items():
            config_field = config_by_name[field_name]
            expected_type = _configured_sql_type(config_field)
            if ddl_field["type"] != expected_type:
                raise ValueError(
                    f"{table_name}.{field_name} type differs between DDL and config: "
                    f"ddl={ddl_field['type']}, config={expected_type}"
                )
            if ddl_field["nullable"] != bool(config_field["nullable"]):
                raise ValueError(
                    f"{table_name}.{field_name} nullability differs between DDL "
                    f"and config: ddl={ddl_field['nullable']}, "
                    f"config={config_field['nullable']}"
                )
            if ddl_field["default"] != _normalized_default(config_field.get("default")):
                raise ValueError(
                    f"{table_name}.{field_name} default differs between DDL and "
                    f"config: ddl={ddl_field['default']}, "
                    f"config={_normalized_default(config_field.get('default'))}"
                )

    constraints = _ddl_constraints(schema_source)
    configured_primary_keys = {
        table_name: tuple(_primary_key_fields(table_config))
        for table_name, table_config in configured_tables.items()
    }
    if constraints["primary_keys"] != configured_primary_keys:
        raise ValueError(
            "DDL primary keys differ from CRM config: "
            f"ddl={constraints['primary_keys']}, "
            f"config={configured_primary_keys}"
        )

    configured_unique_keys = {
        (table_name, field["name"])
        for table_name, table_config in configured_tables.items()
        for field in table_config["fields"]
        if field.get("key") == "unique"
    }
    if constraints["unique_keys"] != configured_unique_keys:
        raise ValueError(
            "DDL unique keys differ from CRM config: "
            f"ddl={sorted(constraints['unique_keys'])}, "
            f"config={sorted(configured_unique_keys)}"
        )

    configured_foreign_keys = _configured_foreign_keys(crm_config)
    if constraints["foreign_keys"] != configured_foreign_keys:
        raise ValueError(
            "DDL foreign keys differ from CRM config: "
            f"ddl={sorted(constraints['foreign_keys'])}, "
            f"config={sorted(configured_foreign_keys)}"
        )


def _validate_relationships(crm_config: dict[str, Any]) -> None:
    """Validate relationship endpoints and configured foreign-key coverage."""

    configured_tables = crm_config["tables"]
    relationships = crm_config.get("relationships", [])
    relationship_keys: set[tuple[str, str, str, str]] = set()
    for relationship in relationships:
        parent_table = relationship["parent_table"]
        child_table = relationship["child_table"]
        parent_field = relationship["parent_field"]
        child_field = relationship["child_field"]
        if parent_table not in configured_tables:
            raise ValueError(f"Unknown relationship parent table: {parent_table}")
        if child_table not in configured_tables:
            raise ValueError(f"Unknown relationship child table: {child_table}")
        if parent_field not in _configured_field_names(configured_tables[parent_table]):
            raise ValueError(
                f"Unknown relationship parent field: {parent_table}.{parent_field}"
            )
        if child_field not in _configured_field_names(configured_tables[child_table]):
            raise ValueError(
                f"Unknown relationship child field: {child_table}.{child_field}"
            )
        if relationship["join_type"] not in {"inner", "left"}:
            raise ValueError(
                f"Unsupported relationship join_type: {relationship['join_type']}"
            )
        relationship_keys.add((child_table, child_field, parent_table, parent_field))

    if len(relationship_keys) != len(relationships):
        raise ValueError("CRM relationships contain duplicate foreign-key paths")
    configured_foreign_keys = _configured_foreign_keys(crm_config)
    if relationship_keys != configured_foreign_keys:
        raise ValueError(
            "CRM relationships must describe every configured foreign key exactly: "
            f"relationships={sorted(relationship_keys)}, "
            f"fields={sorted(configured_foreign_keys)}"
        )


def _validate_primary_keys(crm_config: dict[str, Any]) -> None:
    """Validate each table's primary-key fields and nullability markers."""

    configured_tables = crm_config["tables"]
    for table_name, table_config in configured_tables.items():
        primary_key = table_config["primary_key"]
        primary_key_fields = (
            [primary_key] if isinstance(primary_key, str) else list(primary_key)
        )
        if not primary_key_fields:
            raise ValueError(f"{table_name} must define at least one primary key field")

        fields_by_name = {
            field["name"]: field for field in table_config.get("fields", [])
        }
        for field_name in primary_key_fields:
            if field_name not in fields_by_name:
                raise ValueError(
                    f"{table_name} primary_key references unknown field: {field_name}"
                )
            field_config = fields_by_name[field_name]
            if field_config["nullable"]:
                raise ValueError(
                    f"{table_name}.{field_name} primary_key cannot be nullable"
                )
            if field_config.get("key") not in {"primary", "primary_foreign"}:
                raise ValueError(
                    f"{table_name}.{field_name} primary_key field must be marked "
                    "primary or primary_foreign"
                )


def _validate_domain_values(crm_config: dict[str, Any]) -> None:
    """Require each configured domain-value list to be valid and unique."""

    domain_values = crm_config["domain_values"]
    if not isinstance(domain_values, dict):
        raise ValueError("CRM domain_values must be an object")
    for name, values in domain_values.items():
        if not isinstance(values, list) or not values:
            raise ValueError(f"CRM domain_values.{name} must be a non-empty list")
        if any(not isinstance(value, str) or not value for value in values):
            raise ValueError(f"CRM domain_values.{name} must contain non-empty strings")
        if len(values) != len(set(values)):
            raise ValueError(f"CRM domain_values.{name} contains duplicates")


def _validate_business_mappings(crm_config: dict[str, Any]) -> None:
    """Validate mappings whose keys must cover a configured domain exactly."""

    mappings = crm_config["business_mappings"]
    values = crm_config["domain_values"]
    subject_mapping = mappings.get("support_case_subject_by_category")
    if not isinstance(subject_mapping, dict):
        raise ValueError(
            "CRM business_mappings.support_case_subject_by_category "
            "must be an object"
        )
    categories = set(values["support_case_categories"])
    if set(subject_mapping) != categories:
        raise ValueError(
            "Support-case subject mapping must match support_case_categories: "
            f"expected={sorted(categories)}, actual={sorted(subject_mapping)}"
        )
    if any(
        not isinstance(subject, str) or not subject
        for subject in subject_mapping.values()
    ):
        raise ValueError("Support-case subject mappings must be non-empty strings")

    status_rules = mappings.get("campaign_status_rules")
    if not isinstance(status_rules, dict):
        raise ValueError(
            "CRM business_mappings.campaign_status_rules must be an object"
        )
    campaign_statuses = set(values["campaign_statuses"])
    for rule_name in ("future_start", "requires_end_date"):
        configured_statuses = status_rules.get(rule_name)
        if not isinstance(configured_statuses, list) or not configured_statuses:
            raise ValueError(
                f"campaign_status_rules.{rule_name} must be a non-empty list"
            )
        if len(configured_statuses) != len(set(configured_statuses)):
            raise ValueError(f"campaign_status_rules.{rule_name} contains duplicates")
        unknown = set(configured_statuses) - campaign_statuses
        if unknown:
            raise ValueError(
                f"campaign_status_rules.{rule_name} contains unknown statuses: "
                f"{sorted(unknown)}"
            )

    attribution = mappings.get("attribution")
    if not isinstance(attribution, dict):
        raise ValueError("CRM business_mappings.attribution must be an object")
    scale = attribution.get("weight_scale")
    if not isinstance(scale, int) or isinstance(scale, bool) or scale < 0:
        raise ValueError("Attribution weight_scale must be a non-negative integer")
    try:
        minimum = Decimal(attribution["minimum_weight"])
        maximum = Decimal(attribution["maximum_weight"])
        expected = Decimal(attribution["expected_sum_before_imperfections"])
    except (InvalidOperation, KeyError) as exc:
        raise ValueError("Attribution decimal values must be valid") from exc
    if not minimum <= expected <= maximum:
        raise ValueError("Attribution expected sum must be within its configured range")
    multiplier = 10**scale
    if expected * multiplier != (expected * multiplier).to_integral_value():
        raise ValueError("Attribution expected sum exceeds configured weight_scale")
    maximum_primary = attribution.get("maximum_primary_campaigns_per_contact")
    if (
        not isinstance(maximum_primary, int)
        or isinstance(maximum_primary, bool)
        or maximum_primary < 1
    ):
        raise ValueError(
            "Attribution maximum_primary_campaigns_per_contact must be positive"
        )


def _validate_generation_rules(
    base_config: dict[str, Any],
    crm_config: dict[str, Any],
) -> None:
    """Validate configurable base-generation ranges, rates, and weights."""

    rules = crm_config["generation_rules"]
    required_groups = {
        "date_windows",
        "accounts",
        "contacts",
        "campaigns",
        "interactions",
        "support_cases",
    }
    if not isinstance(rules, dict) or set(rules) != required_groups:
        raise ValueError(
            "CRM generation_rules groups differ from the required contract: "
            f"expected={sorted(required_groups)}, actual={sorted(rules or {})}"
        )
    for group_name in required_groups:
        if not isinstance(rules[group_name], dict):
            raise ValueError(f"CRM generation_rules.{group_name} must be an object")

    reference_today = date.fromisoformat(base_config["reference_today"])
    date_windows = rules["date_windows"]
    required_dates = {
        "account_created_start",
        "unlinked_contact_created_start",
        "entity_distribution_start",
        "campaign_historical_start",
        "organic_interaction_start",
        "support_case_opened_start",
    }
    if not isinstance(date_windows, dict) or set(date_windows) != required_dates:
        raise ValueError("CRM generation_rules.date_windows keys are invalid")
    parsed_dates: dict[str, date] = {}
    for name, value in date_windows.items():
        try:
            configured_date = date.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"generation_rules.date_windows.{name} is invalid"
            ) from exc
        if configured_date > reference_today:
            raise ValueError(f"generation_rules.date_windows.{name} is after today")
        parsed_dates[name] = configured_date

    account_rules = rules["accounts"]
    _validate_integer_range(account_rules.get("account_size"), "accounts.account_size")
    _validate_weight_mapping(
        account_rules.get("customer_tier_weights"),
        crm_config["domain_values"]["customer_tiers"],
        "accounts.customer_tier_weights",
    )
    _validate_probability(
        account_rules.get("active_probability"), "accounts.active_probability"
    )

    contact_rules = rules["contacts"]
    for name in (
        "eligible_account_fraction",
        "unassigned_account_probability",
        "active_probability",
    ):
        _validate_probability(contact_rules.get(name), f"contacts.{name}")
    _validate_positive_fraction(
        contact_rules["eligible_account_fraction"],
        "contacts.eligible_account_fraction",
    )

    campaign_rules = rules["campaigns"]
    _validate_weight_mapping(
        campaign_rules.get("status_weights"),
        crm_config["domain_values"]["campaign_statuses"],
        "campaigns.status_weights",
    )
    _validate_coverage_values(
        campaign_rules.get("required_status_coverage"),
        crm_config["domain_values"]["campaign_statuses"],
        "campaigns.required_status_coverage",
    )
    _validate_coverage_capacity(
        campaign_rules["required_status_coverage"],
        crm_config["tables"]["campaigns"]["row_targets"],
        "campaigns.required_status_coverage",
    )
    for name in (
        "future_start_offset_days",
        "historical_end_duration_days",
        "future_end_duration_days",
        "creation_lead_days",
    ):
        _validate_integer_range(campaign_rules.get(name), f"campaigns.{name}")
    _validate_positive_integer(
        campaign_rules.get("future_open_window_days"),
        "campaigns.future_open_window_days",
    )
    if parsed_dates["campaign_historical_start"] > reference_today - timedelta(
        days=int(campaign_rules["historical_end_duration_days"]["minimum"])
    ):
        raise ValueError("campaign_historical_start leaves no valid completed window")
    budget = campaign_rules.get("budget")
    if not isinstance(budget, dict):
        raise ValueError("generation_rules.campaigns.budget must be an object")
    _validate_integer_range(
        {
            "minimum": budget.get("minimum_amount"),
            "maximum": budget.get("maximum_amount"),
        },
        "campaigns.budget",
    )
    _validate_probability(
        budget.get("null_probability"),
        "campaigns.budget.null_probability",
    )

    interaction_rules = rules["interactions"]
    for name in (
        "attributed_campaign_fraction",
        "eligible_contact_fraction",
        "organic_probability",
        "account_null_probability",
    ):
        _validate_probability(interaction_rules.get(name), f"interactions.{name}")
    for name in ("attributed_campaign_fraction", "eligible_contact_fraction"):
        _validate_positive_fraction(interaction_rules[name], f"interactions.{name}")
    if float(interaction_rules["organic_probability"]) >= 1:
        raise ValueError("interactions.organic_probability must be below 1")
    if float(interaction_rules["account_null_probability"]) >= 1:
        raise ValueError("interactions.account_null_probability must be below 1")
    _validate_integer_range(
        interaction_rules.get("created_lag_hours"),
        "interactions.created_lag_hours",
        allow_zero_minimum=True,
    )

    case_rules = rules["support_cases"]
    for name in ("eligible_contact_fraction", "contact_assignment_probability"):
        _validate_probability(case_rules.get(name), f"support_cases.{name}")
        _validate_positive_fraction(case_rules[name], f"support_cases.{name}")
    _validate_weight_mapping(
        case_rules.get("status_weights"),
        crm_config["domain_values"]["support_case_statuses"],
        "support_cases.status_weights",
    )
    _validate_weight_mapping(
        case_rules.get("priority_weights"),
        crm_config["domain_values"]["support_case_priorities"],
        "support_cases.priority_weights",
    )
    _validate_coverage_values(
        case_rules.get("required_status_coverage"),
        crm_config["domain_values"]["support_case_statuses"],
        "support_cases.required_status_coverage",
    )
    _validate_coverage_capacity(
        case_rules["required_status_coverage"],
        crm_config["tables"]["support_cases"]["row_targets"],
        "support_cases.required_status_coverage",
    )
    for name in ("resolved_opened_cutoff_days", "minimum_resolution_hours"):
        _validate_positive_integer(case_rules.get(name), f"support_cases.{name}")
    if parsed_dates["support_case_opened_start"] > reference_today - timedelta(
        days=int(case_rules["resolved_opened_cutoff_days"])
    ):
        raise ValueError("support_case_opened_start leaves no resolved-case window")
    multiplier = case_rules.get("resolution_sla_multiplier")
    if (
        not isinstance(multiplier, (int, float))
        or isinstance(multiplier, bool)
        or multiplier <= 0
    ):
        raise ValueError("support_cases.resolution_sla_multiplier must be positive")
    _validate_integer_range(
        case_rules.get("created_lag_hours"),
        "support_cases.created_lag_hours",
        allow_zero_minimum=True,
    )


def _validate_probability(value: Any, path: str) -> None:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not 0 <= float(value) <= 1
    ):
        raise ValueError(f"generation_rules.{path} must be between 0 and 1")


def _validate_positive_fraction(value: Any, path: str) -> None:
    if float(value) <= 0:
        raise ValueError(f"generation_rules.{path} must be greater than zero")


def _validate_weight_mapping(
    mapping: Any,
    domain_values: list[str],
    path: str,
) -> None:
    if not isinstance(mapping, dict) or set(mapping) != set(domain_values):
        raise ValueError(
            f"generation_rules.{path} keys must match configured domain values"
        )
    for value in mapping.values():
        _validate_probability(value, path)
    if abs(sum(float(value) for value in mapping.values()) - 1.0) > 1e-9:
        raise ValueError(f"generation_rules.{path} weights must sum to 1")


def _validate_integer_range(
    value: Any,
    path: str,
    allow_zero_minimum: bool = False,
) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"generation_rules.{path} must be an object")
    minimum = value.get("minimum")
    maximum = value.get("maximum")
    minimum_allowed = 0 if allow_zero_minimum else 1
    if (
        any(
            not isinstance(item, int) or isinstance(item, bool)
            for item in (minimum, maximum)
        )
        or not minimum_allowed <= minimum <= maximum
    ):
        raise ValueError(f"generation_rules.{path} must be a valid integer range")


def _validate_positive_integer(value: Any, path: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"generation_rules.{path} must be a positive integer")


def _validate_coverage_values(
    values: Any,
    domain_values: list[str],
    path: str,
) -> None:
    if (
        not isinstance(values, list)
        or not values
        or len(values) != len(set(values))
        or not set(values).issubset(domain_values)
    ):
        raise ValueError(
            f"generation_rules.{path} must contain unique configured values"
        )


def _validate_coverage_capacity(
    values: list[str],
    row_targets: dict[str, int],
    path: str,
) -> None:
    if any(len(values) > row_count for row_count in row_targets.values()):
        raise ValueError(f"generation_rules.{path} exceeds a configured row target")


def _validate_generation_targets(
    base_config: dict[str, Any],
    crm_config: dict[str, Any],
) -> None:
    """Validate distribution and imperfection targets and their references."""

    tables = crm_config["tables"]
    resolved_distributions = resolve_distribution_settings(
        base_config["distribution_defaults"],
        crm_config["distributions"],
    )
    for target_name, target in crm_config["distribution_targets"].items():
        _validate_target_fields(target_name, target, tables)
        if "sla_source" in target:
            _resolve_config_reference(
                target["sla_source"], base_config, crm_config, target_name
            )
        if "distribution" in target:
            settings_key = target.get("settings_key")
            if not isinstance(settings_key, str) or not settings_key:
                raise ValueError(
                    f"Distribution target {target_name} has no settings_key"
                )
            if settings_key not in resolved_distributions:
                raise ValueError(
                    f"Distribution target {target_name} references unknown settings "
                    f"key: {settings_key}"
                )
            if target["distribution"] != resolved_distributions[settings_key]["name"]:
                raise ValueError(
                    f"{target_name} distribution differs from its resolved settings"
                )

    for target_name, target in crm_config["imperfection_targets"].items():
        _validate_target_fields(target_name, target, tables)
        source_keys = [key for key in ("rate_source", "values_source") if key in target]
        if not source_keys:
            raise ValueError(
                f"Imperfection target {target_name} has no rate or values source"
            )
        for source_key in source_keys:
            value = _resolve_config_reference(
                target[source_key], base_config, crm_config, target_name
            )
            if source_key == "rate_source" and (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not 0 <= float(value) <= 100
            ):
                raise ValueError(f"{target_name}.{source_key} must resolve to 0..100")
            if source_key == "values_source" and (
                not isinstance(value, list) or not value
            ):
                raise ValueError(
                    f"{target_name}.{source_key} must reference a non-empty list"
                )

        minimum = target.get("minimum_value")
        maximum = target.get("maximum_value")
        if minimum is not None and maximum is not None and maximum < minimum:
            raise ValueError(f"{target_name} maximum_value is below minimum_value")


def _resolve_config_reference(
    reference: Any,
    base_config: dict[str, Any],
    crm_config: dict[str, Any],
    context: str,
) -> Any:
    """Resolve a dotted value reference from shared or assembled CRM config.

    `crm.json:` is a logical reference to the complete CRM namespace returned
    by `load_crm_config()`. Physical component filenames are intentionally not
    part of this contract.
    """

    if not isinstance(reference, str) or not reference:
        raise ValueError(f"{context} contains an invalid config reference")

    if ":" in reference:
        source, dotted_path = reference.split(":", 1)
        source_name = Path(source).name
        if source_name == "base.json":
            value: Any = base_config
        elif source_name == "crm.json":
            value = crm_config
        else:
            raise ValueError(f"{context} references unsupported config: {source}")
    else:
        dotted_path = reference
        value = crm_config

    for part in dotted_path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise ValueError(f"{context} references missing config value: {reference}")
        value = value[part]
    return value


def _validate_target_fields(
    target_name: str,
    target: dict[str, Any],
    tables: dict[str, Any],
) -> None:
    """Ensure a generation target references configured tables and fields."""

    table_name = target.get("table")
    if table_name is not None:
        if table_name not in tables:
            raise ValueError(f"{target_name} references unknown table: {table_name}")
        fields = _configured_field_names(tables[table_name])
        referenced_fields = [
            target[key]
            for key in ("field", "group_by", "anchor_field")
            if key in target
        ]
        referenced_fields.extend(target.get("fields", []))
        referenced_fields.extend(target.get("derived_fields", []))
        for field_name in referenced_fields:
            if field_name not in fields:
                raise ValueError(
                    f"{target_name} references unknown field: "
                    f"{table_name}.{field_name}"
                )
    for qualified_field in target.get("targets", []):
        if "." not in qualified_field:
            raise ValueError(f"{target_name} target must be table-qualified")
        qualified_table, field_name = qualified_field.split(".", 1)
        if qualified_table not in tables:
            raise ValueError(
                f"{target_name} references unknown table: {qualified_table}"
            )
        if field_name not in _configured_field_names(tables[qualified_table]):
            raise ValueError(
                f"{target_name} references unknown field: {qualified_field}"
            )


def _validate_join_path_requirements(crm_config: dict[str, Any]) -> None:
    """Validate join-path structure, semantics, and field references."""

    configured_tables = crm_config["tables"]
    join_paths = crm_config.get("join_path_requirements", [])
    join_ids = [join_path.get("id") for join_path in join_paths]
    if any(not join_id for join_id in join_ids):
        raise ValueError("CRM join_path_requirements contains a missing ID")
    if len(join_ids) != len(set(join_ids)):
        raise ValueError("CRM join_path_requirements contains duplicate IDs")

    for join_path in join_paths:
        tables = join_path["tables"]
        if not isinstance(tables, list) or len(tables) < 2:
            raise ValueError(f"{join_path['id']} must include at least two tables")
        if len(tables) != len(set(tables)):
            raise ValueError(f"{join_path['id']} contains duplicate tables")
        for table_name in tables:
            if table_name not in configured_tables:
                raise ValueError(
                    f"{join_path['id']} references unknown table: {table_name}"
                )
        if join_path["join_type"] not in {"inner", "left"}:
            raise ValueError(
                f"{join_path['id']} has unsupported join_type: "
                f"{join_path['join_type']}"
            )
        if join_path["required_result"] not in {
            "non_empty",
            "non_empty_with_unmatched_parent_rows",
        }:
            raise ValueError(
                f"{join_path['id']} has unsupported required_result: "
                f"{join_path['required_result']}"
            )
        if (
            join_path["required_result"] == "non_empty_with_unmatched_parent_rows"
            and "unmatched_condition" not in join_path
        ):
            raise ValueError(
                f"{join_path['id']} requires an unmatched_condition for LEFT checks"
            )
        if (
            join_path["required_result"] == "non_empty_with_unmatched_parent_rows"
            and join_path["join_type"] != "left"
        ):
            raise ValueError(
                f"{join_path['id']} unmatched-parent checks require a LEFT join"
            )
        _validate_condition_fields(
            join_path["id"], join_path.get("join_condition"), tables, configured_tables
        )
        unmatched_condition = join_path.get("unmatched_condition")
        if unmatched_condition is not None:
            _validate_condition_fields(
                join_path["id"], unmatched_condition, tables, configured_tables
            )


def _validate_condition_fields(
    join_id: str,
    condition: Any,
    path_tables: list[str],
    configured_tables: dict[str, Any],
) -> None:
    """Validate table-qualified field references in a join condition."""

    if not isinstance(condition, str) or not condition.strip():
        raise ValueError(f"{join_id} has an invalid join condition")
    references = re.findall(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\b", condition)
    if not references:
        raise ValueError(f"{join_id} condition contains no qualified fields")
    for table_name, field_name in references:
        if table_name not in path_tables:
            raise ValueError(
                f"{join_id} condition references table outside its path: {table_name}"
            )
        if field_name not in _configured_field_names(configured_tables[table_name]):
            raise ValueError(
                f"{join_id} condition references unknown field: "
                f"{table_name}.{field_name}"
            )


def _configured_field_names(table_config: dict[str, Any]) -> set[str]:
    """Return the configured field names for one table."""

    return {field["name"] for field in table_config["fields"]}


def _primary_key_fields(table_config: dict[str, Any]) -> list[str]:
    """Normalize a scalar or composite primary key into a field list."""

    primary_key = table_config["primary_key"]
    return [primary_key] if isinstance(primary_key, str) else list(primary_key)


def _configured_foreign_keys(
    crm_config: dict[str, Any],
) -> set[tuple[str, str, str, str]]:
    """Return configured foreign keys as child-to-parent field tuples."""

    return {
        (
            table_name,
            field["name"],
            field["references"]["table"],
            field["references"]["field"],
        )
        for table_name, table_config in crm_config["tables"].items()
        for field in table_config["fields"]
        if "references" in field
    }


def _configured_sql_type(field: dict[str, Any]) -> str:
    """Convert a configured field type into its normalized SQL form."""

    field_type = str(field["type"]).upper()
    if field_type in {"VARCHAR", "CHAR"}:
        return f"{field_type}({int(field['max_length'])})"
    if field_type == "DECIMAL":
        return f"DECIMAL({int(field['precision'])},{int(field['scale'])})"
    if field_type not in {"INTEGER", "BOOLEAN", "DATE", "TIMESTAMP"}:
        raise ValueError(f"Unsupported CRM field type: {field['type']}")
    return field_type


def _normalized_default(value: Any) -> str | None:
    """Normalize configured and DDL defaults for direct comparison."""

    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).upper()
    return str(value).strip("'").upper()


def _ddl_table_specs(schema_path: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """Parse ordered DDL columns and their core schema metadata."""

    field_pattern = re.compile(
        r"^(\w+)\s+"
        r"(INTEGER|BOOLEAN|DATE|TIMESTAMP|VARCHAR\(\d+\)|CHAR\(\d+\)|"
        r"DECIMAL\(\d+,\s*\d+\))(?=\s|$)(.*)$",
        re.I,
    )
    default_pattern = re.compile(r"\bDEFAULT\s+('(?:[^']|'')*'|TRUE|FALSE)", re.I)
    tables: dict[str, dict[str, dict[str, Any]]] = {}
    for table_name, body in _ddl_table_bodies(schema_path):
        fields: dict[str, dict[str, Any]] = {}
        for raw_line in body.splitlines():
            line = raw_line.strip().rstrip(",")
            match = field_pattern.match(line)
            if not match:
                continue
            field_name, sql_type, remainder = match.groups()
            default_match = default_pattern.search(remainder)
            fields[field_name] = {
                "type": re.sub(r"\s+", "", sql_type.upper()),
                "nullable": "NOT NULL" not in remainder.upper(),
                "default": (
                    _normalized_default(default_match.group(1))
                    if default_match
                    else None
                ),
            }
        tables[table_name] = fields
    return tables


def _ddl_table_bodies(schema_path: Path) -> list[tuple[str, str]]:
    """Extract ordered CREATE TABLE names and bodies from a DDL file."""

    ddl = schema_path.read_text(encoding="utf-8")
    tables = re.findall(r"CREATE TABLE (\w+) \((.*?)\n\);", ddl, re.S | re.I)
    if not tables:
        raise ValueError(f"No CREATE TABLE statements found in {schema_path}")
    return tables


def _ddl_constraints(schema_path: Path) -> dict[str, Any]:
    """Execute the DDL in DuckDB and return normalized constraints."""

    try:
        import duckdb
    except ImportError as exc:
        raise ImportError(
            "DuckDB is required to validate the executable CRM schema"
        ) from exc

    connection = duckdb.connect(":memory:")
    try:
        connection.execute(schema_path.read_text(encoding="utf-8"))
        rows = connection.execute(
            """
            SELECT table_name, constraint_type, constraint_text,
                   constraint_column_names
            FROM duckdb_constraints()
            WHERE schema_name = 'main'
            ORDER BY table_oid, constraint_index
            """
        ).fetchall()
    finally:
        connection.close()

    primary_keys: dict[str, tuple[str, ...]] = {}
    unique_keys: set[tuple[str, str]] = set()
    foreign_keys: set[tuple[str, str, str, str]] = set()
    checks: dict[str, set[str]] = {}
    foreign_key_pattern = re.compile(
        r"FOREIGN KEY \((\w+)\) REFERENCES (\w+)\((\w+)\)", re.I
    )
    for table_name, constraint_type, constraint_text, column_names in rows:
        if constraint_type == "PRIMARY KEY":
            primary_keys[table_name] = tuple(column_names)
        elif constraint_type == "UNIQUE":
            if len(column_names) != 1:
                raise ValueError(
                    f"Unsupported composite UNIQUE constraint on {table_name}"
                )
            unique_keys.add((table_name, column_names[0]))
        elif constraint_type == "FOREIGN KEY":
            match = foreign_key_pattern.fullmatch(constraint_text)
            if not match:
                raise ValueError(f"Unable to parse DDL foreign key: {constraint_text}")
            child_field, parent_table, parent_field = match.groups()
            foreign_keys.add((table_name, child_field, parent_table, parent_field))
        elif constraint_type == "CHECK":
            checks.setdefault(table_name, set()).add(_compact_sql(constraint_text))
    return {
        "primary_keys": primary_keys,
        "unique_keys": unique_keys,
        "foreign_keys": foreign_keys,
        "checks": checks,
    }


def _compact_sql(value: str) -> str:
    """Normalize SQL text for whitespace-insensitive comparisons."""

    return re.sub(r"[\s()]", "", value).lower()
