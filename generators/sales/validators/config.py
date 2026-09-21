"""Sales configuration, schema, and generation-rule validation."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from generators.core.base import GenerationSettings
from generators.core.base import PROJECT_ROOT
from generators.core.base import resolve_distribution_settings
from generators.core.imperfections import count_from_pct
from generators.core.schema_contract import configured_field_names
from generators.core.schema_contract import configured_foreign_keys
from generators.core.schema_contract import configured_sql_type
from generators.core.schema_contract import ddl_constraints
from generators.core.schema_contract import ddl_table_specs
from generators.core.schema_contract import header_table_specs
from generators.core.schema_contract import normalized_default
from generators.core.schema_contract import primary_key_fields
from generators.sales import config as sales_config_module


REQUIRED_SECTIONS = {
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
EXPECTED_JOIN_PATH_IDS = [f"sales_jp_{index:03d}" for index in range(1, 9)]
USD_TABLES = ("deals", "products", "targets")


def validate_sales_config() -> None:
    """Validate the complete Sales contract before generation starts."""

    base_config = sales_config_module.load_base_config()
    sales_config = sales_config_module.load_sales_config()
    _validate_required_sections(sales_config)
    _validate_fixed_references(base_config, sales_config)
    _validate_schema_alignment(sales_config)
    _validate_primary_keys(sales_config)
    _validate_relationships(sales_config)
    _validate_domain_values(sales_config)
    _validate_generation_rules(base_config, sales_config)
    _validate_business_mappings(sales_config)
    _validate_generation_targets(base_config, sales_config)
    _validate_join_paths(sales_config)
    _validate_usd_policy(sales_config)
    for profile in base_config["profiles"]:
        GenerationSettings.from_configs(base_config, sales_config, profile)


def _validate_required_sections(config: dict[str, Any]) -> None:
    """Validate required sections and basic table/field shape."""

    missing = REQUIRED_SECTIONS - set(config)
    if missing:
        raise ValueError(f"Sales config is missing sections: {sorted(missing)}")
    if config.get("domain") != "sales":
        raise ValueError("Sales config domain must equal 'sales'")

    table_order = config.get("table_order")
    tables = config.get("tables")
    if not isinstance(table_order, list) or not table_order:
        raise ValueError("Sales table_order must be a non-empty list")
    if len(table_order) != len(set(table_order)):
        raise ValueError("Sales table_order contains duplicates")
    if not isinstance(tables, dict) or set(tables) != set(table_order):
        raise ValueError("Sales tables must match table_order exactly")

    for table_name in table_order:
        table = tables[table_name]
        missing_table = {"primary_key", "role", "row_targets", "fields"} - set(table)
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


def _validate_fixed_references(
    base_config: dict[str, Any],
    config: dict[str, Any],
) -> None:
    """Validate shared-value references and release-profile agreement."""

    expected = {
        "seed_source": "config/generation/base.json:seed",
        "reference_today_source": "config/generation/base.json:reference_today",
    }
    for name, reference in expected.items():
        if config["fixed_values"].get(name) != reference:
            raise ValueError(f"Sales fixed_values.{name} must reference {reference}")
        _resolve_reference(reference, base_config, config, name)
    if not config["fixed_values"].get("manifest_generated_at"):
        raise ValueError("Sales manifest_generated_at is required")
    if config["generation_notes"].get("row_cap_source") != (
        "config/generation/base.json:max_rows_per_table"
    ):
        raise ValueError("Sales row cap must reference shared base config")
    if base_config["release_profile"] != config["release_rules"]["release_profile"]:
        raise ValueError("release_profile differs between base and Sales config")


def _validate_schema_alignment(config: dict[str, Any]) -> None:
    """Compare config with canonical DDL and signed CSV header fields."""

    schema_path = PROJECT_ROOT / config["schema_source"]
    header_path = schema_path.with_name("sales_csv_header_spec.md")
    ddl = ddl_table_specs(schema_path)
    headers = header_table_specs(header_path)
    table_order = config["table_order"]
    if list(ddl) != table_order or list(headers) != table_order:
        raise ValueError(
            "Sales table order differs across DDL, header spec, and config"
        )

    for table_name in table_order:
        configured_fields = config["tables"][table_name]["fields"]
        configured_names = [field["name"] for field in configured_fields]
        if list(ddl[table_name]) != configured_names:
            raise ValueError(f"{table_name} fields differ between DDL and config")
        if [field["name"] for field in headers[table_name]] != configured_names:
            raise ValueError(f"{table_name} fields differ between header and config")
        by_name = {field["name"]: field for field in configured_fields}
        for field_name, ddl_field in ddl[table_name].items():
            configured = by_name[field_name]
            expected_type = configured_sql_type(configured)
            expected_nullable = bool(configured["nullable"])
            expected_default = normalized_default(configured.get("default"))
            if ddl_field != {
                "type": expected_type,
                "nullable": expected_nullable,
                "default": expected_default,
            }:
                raise ValueError(
                    f"{table_name}.{field_name} differs between DDL and config"
                )
            header_field = next(
                field for field in headers[table_name] if field["name"] == field_name
            )
            if header_field["type"] != expected_type or (
                header_field["nullable"] != expected_nullable
            ):
                raise ValueError(
                    f"{table_name}.{field_name} differs between header and config"
                )

    constraints = ddl_constraints(schema_path)
    expected_primary = {
        table_name: tuple(primary_key_fields(table))
        for table_name, table in config["tables"].items()
    }
    if constraints["primary_keys"] != expected_primary:
        raise ValueError("Sales DDL primary keys differ from config")
    if constraints["unique_keys"]:
        raise ValueError("Sales DDL has unconfigured UNIQUE constraints")
    if constraints["foreign_keys"] != configured_foreign_keys(config):
        raise ValueError("Sales DDL foreign keys differ from config")


def _validate_primary_keys(config: dict[str, Any]) -> None:
    """Validate primary-key references, markers, and nullability."""

    for table_name, table in config["tables"].items():
        fields = {field["name"]: field for field in table["fields"]}
        for field_name in primary_key_fields(table):
            if field_name not in fields:
                raise ValueError(f"{table_name} primary key references unknown field")
            if fields[field_name]["nullable"]:
                raise ValueError(f"{table_name}.{field_name} primary key is nullable")
            if fields[field_name].get("key") not in {
                "primary",
                "primary_foreign",
            }:
                raise ValueError(f"{table_name}.{field_name} lacks primary key marker")


def _validate_relationships(config: dict[str, Any]) -> None:
    """Validate physical FK coverage and analytical relationship endpoints."""

    tables = config["tables"]
    physical: set[tuple[str, str, str, str]] = set()
    analytical_count = 0
    for relationship in config["relationships"]:
        parent_table = relationship.get("parent_table")
        child_table = relationship.get("child_table")
        parent_field = relationship.get("parent_field")
        child_field = relationship.get("child_field")
        if parent_table not in tables or child_table not in tables:
            raise ValueError("Sales relationship references an unknown table")
        if parent_field not in configured_field_names(tables[parent_table]) or (
            child_field not in configured_field_names(tables[child_table])
        ):
            raise ValueError("Sales relationship references an unknown field")
        relationship_type = relationship.get("relationship_type")
        key = (child_table, child_field, parent_table, parent_field)
        if relationship_type == "foreign_key":
            physical.add(key)
        elif relationship_type == "analytical":
            analytical_count += 1
            if relationship.get("enforced_as_foreign_key") is not False:
                raise ValueError("Analytical Sales relationship cannot be an FK")
            if not relationship.get("additional_condition"):
                raise ValueError("Analytical Sales relationship needs a condition")
        else:
            raise ValueError(f"Unsupported relationship type: {relationship_type}")
    if physical != configured_foreign_keys(config):
        raise ValueError("Sales physical relationships must cover configured FKs")
    if analytical_count != 1:
        raise ValueError("Sales must define one analytical quota relationship")


def _validate_domain_values(config: dict[str, Any]) -> None:
    """Require every domain-value collection to be non-empty and unique."""

    values = config["domain_values"]
    if not isinstance(values, dict) or not values:
        raise ValueError("Sales domain_values must be a non-empty object")
    for name, options in values.items():
        if not isinstance(options, list) or not options:
            raise ValueError(f"Sales domain_values.{name} must be non-empty")
        if any(not isinstance(option, str) or not option for option in options):
            raise ValueError(f"Sales domain_values.{name} contains invalid values")
        if len(options) != len(set(options)):
            raise ValueError(f"Sales domain_values.{name} contains duplicates")


def _validate_generation_rules(
    base_config: dict[str, Any],
    config: dict[str, Any],
) -> None:
    """Validate Sales ranges, weights, dates, and profile capacities."""

    rules = config["generation_rules"]
    required = {
        "date_windows",
        "representatives",
        "leads",
        "deals",
        "products",
        "quotations",
        "targets",
    }
    if set(rules) != required:
        raise ValueError("Sales generation_rules groups differ from contract")
    reference_today = date.fromisoformat(base_config["reference_today"])
    for name, value in rules["date_windows"].items():
        try:
            parsed = date.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid Sales date window: {name}") from exc
        if parsed > reference_today:
            raise ValueError(f"Sales date window {name} is after reference_today")

    _validate_integer_range(rules["leads"]["score"], "leads.score", minimum=0)
    if rules["leads"]["score"]["maximum"] > 100:
        raise ValueError("Sales lead score cannot exceed 100")
    _validate_weights(
        rules["leads"]["status_weights"],
        config["domain_values"]["lead_statuses"],
        "leads.status_weights",
    )
    _validate_coverage(
        rules["leads"]["required_status_coverage"],
        config["domain_values"]["lead_statuses"],
        "leads.required_status_coverage",
    )
    _validate_weights(
        rules["deals"]["stage_weights"],
        config["domain_values"]["deal_stages"],
        "deals.stage_weights",
    )
    for name in ("required_stage_coverage", "open_stages", "closed_stages"):
        _validate_coverage(
            rules["deals"][name],
            config["domain_values"]["deal_stages"],
            f"deals.{name}",
        )
    if set(rules["deals"]["open_stages"]) & set(rules["deals"]["closed_stages"]):
        raise ValueError("Sales open and closed deal stages overlap")

    for path, value in (
        (
            "deals.expected_close_offset_days",
            rules["deals"]["expected_close_offset_days"],
        ),
        ("deals.closed_date_lag_days", rules["deals"]["closed_date_lag_days"]),
        ("quotations.quantity", rules["quotations"]["quantity"]),
        ("quotations.discount_pct", rules["quotations"]["discount_pct"]),
        (
            "quotations.unit_price_adjustment_pct",
            rules["quotations"]["unit_price_adjustment_pct"],
        ),
        (
            "quotations.quote_number_group_size",
            rules["quotations"]["quote_number_group_size"],
        ),
        (
            "quotations.created_to_quoted_lag_days",
            rules["quotations"]["created_to_quoted_lag_days"],
        ),
        ("targets.creation_lead_days", rules["targets"]["creation_lead_days"]),
    ):
        _validate_numeric_range(value, path)
    _validate_amount_range(rules["products"]["list_price"], "products.list_price")
    _validate_probability(
        rules["products"]["active_probability"],
        "products.active_probability",
    )

    profiles = set(base_config["profiles"])
    pools = rules["representatives"]["pool_sizes"]
    periods = rules["targets"]["periods_per_representative"]
    if set(pools) != profiles or set(periods) != profiles:
        raise ValueError("Sales representative and target profiles are incomplete")
    if not isinstance(rules["targets"]["period_months"], int) or (
        rules["targets"]["period_months"] <= 0
    ):
        raise ValueError("Sales target period_months must be positive")
    if rules["targets"]["start_inclusive"] is not True or (
        rules["targets"]["end_exclusive"] is not True
    ):
        raise ValueError("Sales target periods must be start-inclusive/end-exclusive")
    for profile in profiles:
        pool_size = pools[profile]
        period_count = periods[profile]
        if not _positive_integer(pool_size) or not _positive_integer(period_count):
            raise ValueError(f"Sales representative capacity is invalid for {profile}")
        target_rows = config["tables"]["targets"]["row_targets"][profile]
        if pool_size * period_count != target_rows:
            raise ValueError(
                f"Sales target rows do not equal representative-period capacity for {profile}"
            )


def _validate_business_mappings(config: dict[str, Any]) -> None:
    """Validate quota, conversion, bridge, and currency semantics."""

    mappings = config["business_mappings"]
    values = config["domain_values"]
    if mappings.get("currency_code") != "USD":
        raise ValueError("Sales business currency must be USD")
    quota = mappings["quota_attainment"]
    if quota["attained_stage"] not in values["deal_stages"]:
        raise ValueError("Sales attained stage is not a configured deal stage")
    if quota["period_boundary"] != "start_inclusive_end_exclusive":
        raise ValueError("Sales quota period boundary is invalid")
    if (
        mappings["lead_conversion"]["deal_bearing_status"]
        not in (values["lead_statuses"])
    ):
        raise ValueError("Sales deal-bearing lead status is not configured")
    bridge = mappings["quotation_many_to_many"]
    if bridge.get("bridge_table") != "quotations" or not all(
        bridge.get(name) is True
        for name in ("require_multi_product_deal", "require_multi_deal_product")
    ):
        raise ValueError("Sales quotation bridge requirements are incomplete")
    if mappings["quotation_currency"].get("unit_price_currency") != "USD":
        raise ValueError("Sales quotation unit prices must use USD")


def _validate_generation_targets(
    base_config: dict[str, Any],
    config: dict[str, Any],
) -> None:
    """Validate row counts, distribution targets, and imperfection targets."""

    tables = config["tables"]
    profiles = set(base_config["profiles"])
    for table_name, table in tables.items():
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
    for target_name, target in config["distribution_targets"].items():
        settings_key = target["settings_key"]
        if settings_key not in resolved:
            raise ValueError(f"{target_name} references unknown distribution settings")
        if target["distribution"] != resolved[settings_key]["name"]:
            raise ValueError(f"{target_name} distribution algorithm differs")
        table_name = target.get("table")
        if table_name is not None:
            _require_field(
                config, table_name, target.get("field") or target.get("group_by")
            )
        for qualified in target.get("targets", []):
            _require_qualified_field(config, qualified)

    imperfections = config["imperfection_targets"]
    missing = imperfections["missing_product_prices"]
    missing_field = _require_field(config, missing["table"], missing["field"])
    if not missing_field["nullable"]:
        raise ValueError("Sales missing-value target must be nullable")
    duplicate = imperfections["near_duplicate_quotation_lines"]
    for field_name in duplicate["business_key_fields"] + duplicate["variation_fields"]:
        _require_field(config, duplicate["table"], field_name)
    outlier = imperfections["deal_amount_outliers"]
    outlier_field = _require_field(config, outlier["table"], outlier["field"])
    if outlier_field["type"] != "decimal" or outlier_field["nullable"]:
        raise ValueError("Sales outlier target must be a required decimal")
    if outlier["minimum_value"] <= resolved["deal_amount"]["max_amount"] or (
        outlier["maximum_value"] < outlier["minimum_value"]
    ):
        raise ValueError("Sales deal outlier range is invalid")
    boundary = imperfections["product_created_boundary_timestamps"]
    boundary_field = _require_field(config, boundary["table"], boundary["field"])
    if boundary_field["type"] != "timestamp":
        raise ValueError("Sales boundary target must be a timestamp")

    for name, target in imperfections.items():
        reference = target.get("rate_source") or target.get("values_source")
        _resolve_reference(reference, base_config, config, name)

    duplicate_pct = _resolve_reference(
        duplicate["rate_source"], base_config, config, "quotation duplicates"
    )
    for profile in profiles:
        base_rows = tables["quotations"]["row_targets"][profile]
        final_rows = base_rows + count_from_pct(base_rows, float(duplicate_pct))
        if final_rows > base_config["max_rows_per_table"]:
            raise ValueError(f"Sales final quotations exceed row cap for {profile}")


def _validate_join_paths(config: dict[str, Any]) -> None:
    """Validate declared join IDs, endpoints, conditions, and results."""

    paths = config["join_path_requirements"]
    ids = [path.get("id") for path in paths]
    if ids != EXPECTED_JOIN_PATH_IDS:
        raise ValueError(
            f"Sales join path IDs differ: expected={EXPECTED_JOIN_PATH_IDS}, actual={ids}"
        )
    tables = config["tables"]
    for path in paths:
        path_tables = path.get("tables")
        if not isinstance(path_tables, list) or len(path_tables) < 2:
            raise ValueError(f"{path['id']} must contain at least two tables")
        if any(table_name not in tables for table_name in path_tables):
            raise ValueError(f"{path['id']} references an unknown table")
        if path.get("required_result") not in {
            "non_empty",
            "non_empty_with_unmatched_parent_rows",
        }:
            raise ValueError(f"{path['id']} has unsupported result requirement")
        if path["required_result"] == "non_empty_with_unmatched_parent_rows" and (
            not path.get("unmatched_condition")
        ):
            raise ValueError(f"{path['id']} lacks an unmatched condition")
        _validate_condition(path["id"], path["join_condition"], path_tables, tables)
        if path.get("unmatched_condition"):
            _validate_condition(
                path["id"], path["unmatched_condition"], path_tables, tables
            )


def _validate_usd_policy(config: dict[str, Any]) -> None:
    """Require config defaults and executable checks for strict USD-only data."""

    constraints = ddl_constraints(PROJECT_ROOT / config["schema_source"])
    for table_name in USD_TABLES:
        field = _require_field(config, table_name, "currency_code")
        if field.get("default") != "USD" or field["nullable"]:
            raise ValueError(f"{table_name}.currency_code must be required USD")
        checks = constraints["checks"].get(table_name, set())
        if not any("currency_code='usd'" in check for check in checks):
            raise ValueError(f"{table_name} DDL does not enforce USD")


def _resolve_reference(
    reference: Any,
    base_config: dict[str, Any],
    sales_config: dict[str, Any],
    context: str,
) -> Any:
    """Resolve a dotted reference against shared or assembled Sales config."""

    if not isinstance(reference, str) or not reference:
        raise ValueError(f"{context} has an invalid config reference")
    if ":" in reference:
        source, dotted_path = reference.split(":", 1)
        source_name = Path(source).name
        if source_name == "base.json":
            current: Any = base_config
        elif source_name == "sales.json":
            current = sales_config
        else:
            raise ValueError(f"{context} references unsupported config: {source}")
    else:
        dotted_path = reference
        current = sales_config
    for part in dotted_path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise ValueError(f"{context} references missing config value: {reference}")
        current = current[part]
    return current


def _require_field(
    config: dict[str, Any], table_name: Any, field_name: Any
) -> dict[str, Any]:
    """Return a configured field or raise a contextual validation error."""

    if table_name not in config["tables"]:
        raise ValueError(f"Unknown Sales table: {table_name}")
    for field in config["tables"][table_name]["fields"]:
        if field["name"] == field_name:
            return field
    raise ValueError(f"Unknown Sales field: {table_name}.{field_name}")


def _require_qualified_field(config: dict[str, Any], value: Any) -> None:
    """Validate a `table.field` reference."""

    if not isinstance(value, str) or value.count(".") != 1:
        raise ValueError(f"Invalid Sales field reference: {value}")
    _require_field(config, *value.split("."))


def _validate_condition(
    path_id: str,
    condition: Any,
    path_tables: list[str],
    tables: dict[str, Any],
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
        if field_name not in configured_field_names(tables[table_name]):
            raise ValueError(f"{path_id} condition references unknown field")


def _validate_weights(mapping: Any, values: list[str], path: str) -> None:
    """Validate complete non-negative weights that sum to one."""

    if not isinstance(mapping, dict) or set(mapping) != set(values):
        raise ValueError(f"{path} must cover its configured domain values")
    if any(
        not isinstance(weight, (int, float)) or weight < 0
        for weight in mapping.values()
    ):
        raise ValueError(f"{path} contains invalid weights")
    if abs(sum(mapping.values()) - 1.0) > 1e-9:
        raise ValueError(f"{path} weights must sum to 1")


def _validate_coverage(values: Any, domain: list[str], path: str) -> None:
    """Validate a non-empty, unique subset of configured values."""

    if not isinstance(values, list) or not values or len(values) != len(set(values)):
        raise ValueError(f"{path} must be a non-empty unique list")
    unknown = set(values) - set(domain)
    if unknown:
        raise ValueError(f"{path} contains unknown values: {sorted(unknown)}")


def _validate_integer_range(value: Any, path: str, minimum: int = 1) -> None:
    """Validate an inclusive integer range."""

    _validate_numeric_range(value, path)
    if (
        not all(
            isinstance(value[name], int) and not isinstance(value[name], bool)
            for name in ("minimum", "maximum")
        )
        or value["minimum"] < minimum
    ):
        raise ValueError(f"{path} must be an integer range starting at {minimum}")


def _validate_numeric_range(value: Any, path: str) -> None:
    """Validate an object containing an ordered numeric minimum and maximum."""

    if not isinstance(value, dict) or not {"minimum", "maximum"} <= set(value):
        raise ValueError(f"{path} must define minimum and maximum")
    minimum = value["minimum"]
    maximum = value["maximum"]
    if (
        any(
            not isinstance(item, (int, float)) or isinstance(item, bool)
            for item in (minimum, maximum)
        )
        or minimum > maximum
    ):
        raise ValueError(f"{path} has an invalid range")


def _validate_amount_range(value: Any, path: str) -> None:
    """Validate a positive fixed-scale monetary range."""

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


def _validate_probability(value: Any, path: str) -> None:
    """Validate a probability in the inclusive range zero to one."""

    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not 0 <= value <= 1
    ):
        raise ValueError(f"{path} must be between 0 and 1")


def _positive_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


__all__ = ["validate_sales_config"]
