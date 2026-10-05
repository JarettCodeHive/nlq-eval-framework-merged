"""Logistics configuration and schema-contract validation."""

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
from generators.logistics import config as logistics_config_module


REQUIRED_SECTIONS = {
    "domain",
    "release_version",
    "schema_source",
    "semantic_contract",
    "fixed_values",
    "output_paths",
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
    "physical_relationships",
    "analytical_relationships",
    "orphan_exclusions",
    "join_path_requirements",
    "date_rules",
    "decimal_rules",
    "currency_rules",
    "imperfection_rules",
    "consistency_rules",
    "release_validation_rules",
}
EXPECTED_JOIN_PATH_IDS = [f"logistics_jp_{index:03d}" for index in range(1, 7)]
USD_TABLES = ("carriers", "orders", "shipments")


def validate_logistics_config() -> None:
    """Validate the complete Logistics contract before generation starts."""

    base_config = logistics_config_module.load_base_config()
    config = logistics_config_module.load_logistics_config()
    _validate_required_sections(config)
    _validate_explicit_references(base_config, config)
    _validate_fixed_references(base_config, config)
    _validate_schema_alignment(config)
    _validate_primary_keys(config)
    _validate_relationships(config)
    _validate_domain_values(config)
    _validate_generation_rules(base_config, config)
    _validate_generation_targets(base_config, config)
    _validate_join_paths(config)
    _validate_decimal_and_currency_policy(config)
    for profile in base_config["profiles"]:
        GenerationSettings.from_configs(base_config, config, profile)


def _validate_required_sections(config: dict[str, Any]) -> None:
    """Validate top-level sections and dynamic table structure."""

    missing = REQUIRED_SECTIONS - set(config)
    if missing:
        raise ValueError(f"Logistics config is missing sections: {sorted(missing)}")
    if config.get("domain") != "logistics":
        raise ValueError("Logistics config domain must equal 'logistics'")
    table_order = config.get("table_order")
    tables = config.get("tables")
    if not isinstance(table_order, list) or not table_order:
        raise ValueError("Logistics table_order must be a non-empty list")
    if len(table_order) != len(set(table_order)):
        raise ValueError("Logistics table_order contains duplicates")
    if not isinstance(tables, dict) or list(tables) != table_order:
        raise ValueError("Logistics tables must match table_order exactly")
    for table_name, table in tables.items():
        missing_table = {"primary_key", "role", "row_targets", "fields"} - set(table)
        if missing_table:
            raise ValueError(
                f"{table_name} is missing config keys: {sorted(missing_table)}"
            )
        fields = table["fields"]
        names = [field.get("name") for field in fields]
        if (
            not fields
            or any(not name for name in names)
            or len(names) != len(set(names))
        ):
            raise ValueError(f"{table_name}.fields has missing or duplicate names")
        for field in fields:
            missing_field = {"name", "type", "nullable"} - set(field)
            if missing_field:
                raise ValueError(
                    f"{table_name}.{field.get('name', '<unnamed>')} is missing "
                    f"config keys: {sorted(missing_field)}"
                )


def _validate_fixed_references(
    base_config: dict[str, Any], config: dict[str, Any]
) -> None:
    """Validate shared fixed references and release policy agreement."""

    expected = {
        "seed_source": "config/generation/base.json:seed",
        "reference_today_source": "config/generation/base.json:reference_today",
    }
    for name, reference in expected.items():
        if config["fixed_values"].get(name) != reference:
            raise ValueError(
                f"Logistics fixed_values.{name} must reference {reference}"
            )
    if not config["fixed_values"].get("manifest_generated_at"):
        raise ValueError("Logistics manifest_generated_at is required")
    if config["generation_notes"].get("row_cap_source") != (
        "config/generation/base.json:max_rows_per_table"
    ):
        raise ValueError("Logistics row cap must reference shared base config")
    if base_config["release_profile"] != config["release_rules"]["release_profile"]:
        raise ValueError("release_profile differs between base and Logistics config")


def _validate_schema_alignment(config: dict[str, Any]) -> None:
    """Compare Logistics config with canonical DDL and CSV headers."""

    schema_path = PROJECT_ROOT / config["schema_source"]
    header_path = schema_path.with_name("logistics_csv_header_spec.md")
    semantic_path = PROJECT_ROOT / config["semantic_contract"]
    if not semantic_path.is_file():
        raise ValueError("Logistics semantic contract does not exist")
    ddl = ddl_table_specs(schema_path)
    headers = header_table_specs(header_path)
    if list(ddl) != config["table_order"] or list(headers) != config["table_order"]:
        raise ValueError(
            "Logistics table order differs across DDL, header spec, and config"
        )
    for table_name in config["table_order"]:
        configured_fields = config["tables"][table_name]["fields"]
        configured_names = [field["name"] for field in configured_fields]
        if list(ddl[table_name]) != configured_names:
            raise ValueError(f"{table_name} fields differ between DDL and config")
        headers_by_name = {field["name"]: field for field in headers[table_name]}
        if list(headers_by_name) != configured_names:
            raise ValueError(f"{table_name} fields differ between header and config")
        for field in configured_fields:
            name = field["name"]
            expected_type = configured_sql_type(field)
            expected_nullable = bool(field["nullable"])
            if ddl[table_name][name] != {
                "type": expected_type,
                "nullable": expected_nullable,
                "default": normalized_default(field.get("default")),
            }:
                raise ValueError(f"{table_name}.{name} differs between DDL and config")
            if headers_by_name[name] != {
                "name": name,
                "type": expected_type,
                "nullable": expected_nullable,
            }:
                raise ValueError(
                    f"{table_name}.{name} differs between header and config"
                )
    constraints = ddl_constraints(schema_path)
    configured_primary = {
        table_name: tuple(primary_key_fields(table))
        for table_name, table in config["tables"].items()
    }
    if constraints["primary_keys"] != configured_primary:
        raise ValueError("Logistics DDL primary keys differ from config")
    if constraints["foreign_keys"] != configured_foreign_keys(config):
        raise ValueError("Logistics DDL foreign keys differ from config")


def _validate_primary_keys(config: dict[str, Any]) -> None:
    """Validate configured scalar primary keys and markers."""

    for table_name, table in config["tables"].items():
        keys = primary_key_fields(table)
        if len(keys) != 1:
            raise ValueError(f"{table_name} must have one scalar primary key")
        field = _require_field(config, table_name, keys[0])
        if field["nullable"] or field.get("key") != "primary":
            raise ValueError(f"{table_name}.{keys[0]} has an invalid primary key")


def _validate_relationships(config: dict[str, Any]) -> None:
    """Validate physical and deliberately orphanable relationships."""

    physical: set[tuple[str, str, str, str]] = set()
    analytical: list[dict[str, Any]] = []
    for relationship in config["relationships"]:
        parent_table = relationship.get("parent_table")
        parent_field = relationship.get("parent_field")
        child_table = relationship.get("child_table")
        child_field = relationship.get("child_field")
        _require_field(config, parent_table, parent_field)
        _require_field(config, child_table, child_field)
        if relationship.get("relationship_type") == "foreign_key":
            physical.add((child_table, child_field, parent_table, parent_field))
        elif relationship.get("relationship_type") == "analytical_orphanable":
            analytical.append(relationship)
            if (
                relationship.get("enforced_as_foreign_key") is not False
                or relationship.get("allows_null") is not True
                or relationship.get("allows_declared_orphan") is not True
            ):
                raise ValueError("Logistics analytical relationship is incomplete")
        else:
            raise ValueError("Unsupported Logistics relationship type")
    if physical != configured_foreign_keys(config):
        raise ValueError("Logistics relationships must cover configured FKs")
    configured_physical = {
        (
            item["child_table"],
            item["child_field"],
            item["parent_table"],
            item["parent_field"],
        )
        for item in config["physical_relationships"]
    }
    if configured_physical != physical:
        raise ValueError("Logistics physical relationship sections differ")
    if len(analytical) != 1 or len(config["analytical_relationships"]) != 1:
        raise ValueError("Logistics must define one orphanable relationship")
    if config["orphan_exclusions"][0].get("unexpected_orphans_allowed") is not False:
        raise ValueError("Logistics unexpected warehouse orphans cannot be allowed")


def _validate_domain_values(config: dict[str, Any]) -> None:
    """Require non-empty, unique, ASCII Logistics domain-value lists."""

    values = config["domain_values"]
    if not isinstance(values, dict) or not values:
        raise ValueError("Logistics domain_values must be a non-empty object")
    for name, options in values.items():
        if not isinstance(options, list) or not options:
            raise ValueError(f"Logistics domain_values.{name} must be non-empty")
        if len(options) != len(set(options)) or any(
            not isinstance(option, str) or not option or not option.isascii()
            for option in options
        ):
            raise ValueError(f"Logistics domain_values.{name} contains invalid values")


def _validate_generation_rules(
    base_config: dict[str, Any], config: dict[str, Any]
) -> None:
    """Validate Logistics dates, mappings, ranges, weights, and probabilities."""

    rules = config["generation_rules"]
    expected = {
        "date_windows",
        "carriers",
        "warehouses",
        "orders",
        "shipments",
        "inventory",
    }
    if set(rules) != expected:
        raise ValueError("Logistics generation_rules groups differ from contract")
    windows = {
        name: date.fromisoformat(value) for name, value in rules["date_windows"].items()
    }
    if not (
        windows["entity_created_start"]
        <= windows["order_activity_start"]
        <= windows["inventory_activity_start"]
        <= date.fromisoformat(base_config["reference_today"])
    ):
        raise ValueError("Logistics date windows are not chronological")
    _validate_weights(
        rules["orders"]["status_weights"], config["domain_values"]["order_statuses"]
    )
    _validate_weights(
        rules["orders"]["priority_weights"], config["domain_values"]["order_priorities"]
    )
    _validate_weights(
        rules["shipments"]["status_weights"],
        config["domain_values"]["shipment_statuses"],
    )
    for group in ("carriers", "warehouses", "shipments", "inventory"):
        for name, value in rules[group].items():
            if name.endswith("_probability"):
                _validate_probability(value, f"{group}.{name}")
    _validate_probability(
        rules["orders"]["unshipped_order_fraction"],
        "orders.unshipped_order_fraction",
    )
    mappings = config["business_mappings"]
    if mappings.get("currency_code") != "USD":
        raise ValueError("Logistics business currency must be USD")
    if set(mappings["shipment_status_dates"]) != set(
        config["domain_values"]["shipment_statuses"]
    ):
        raise ValueError("Logistics shipment status-date mapping is incomplete")
    if set(mappings["order_status_evidence"]) != set(
        config["domain_values"]["order_statuses"]
    ):
        raise ValueError("Logistics order-status evidence mapping is incomplete")


def _validate_generation_targets(
    base_config: dict[str, Any], config: dict[str, Any]
) -> None:
    """Validate profile rows, distributions, imperfections, and capacities."""

    profiles = set(base_config["profiles"])
    for table_name, table in config["tables"].items():
        if set(table["row_targets"]) != profiles:
            raise ValueError(f"{table_name} row targets differ from profiles")
        if any(
            not _positive_integer(count) or count > base_config["max_rows_per_table"]
            for count in table["row_targets"].values()
        ):
            raise ValueError(f"{table_name} has an invalid row target")
    resolved = resolve_distribution_settings(
        base_config["distribution_defaults"], config["distributions"]
    )
    if set(config["distribution_targets"]) != set(resolved):
        raise ValueError("Logistics distribution targets differ from settings")
    for name, target in config["distribution_targets"].items():
        settings = resolved[target["settings_key"]]
        if target["distribution"] != settings["name"]:
            raise ValueError(f"{name} distribution algorithm differs")
        if target.get("table") and target.get("field"):
            _require_field(config, target["table"], target["field"])
        if target.get("table"):
            for field_name in target.get("dependent_fields", []):
                _require_field(config, target["table"], field_name)
        for qualified in target.get("targets", []):
            _require_qualified_field(config, qualified)
    _validate_imperfections(base_config, config)
    duplicate_pct = float(base_config["imperfections"]["duplicate_pct"])
    for profile in profiles:
        shipment_rows = config["tables"]["shipments"]["row_targets"][profile]
        if (
            shipment_rows + count_from_pct(shipment_rows, duplicate_pct)
            > base_config["max_rows_per_table"]
        ):
            raise ValueError(f"Final shipments exceed row cap for {profile}")


def _validate_imperfections(
    base_config: dict[str, Any], config: dict[str, Any]
) -> None:
    """Validate Logistics imperfection targets and frozen orphan semantics."""

    targets = config["imperfection_targets"]
    expected = {
        "near_duplicate_shipments",
        "missing_order_warehouses",
        "orphaned_order_warehouses",
        "order_total_outliers",
        "coordinated_boundary_dates",
    }
    if set(targets) != expected:
        raise ValueError("Logistics imperfection targets differ from contract")
    duplicate = targets["near_duplicate_shipments"]
    for field_name in duplicate["business_key_fields"] + duplicate["variation_fields"]:
        _require_field(config, duplicate["table"], field_name)
    missing = targets["missing_order_warehouses"]
    orphan = targets["orphaned_order_warehouses"]
    if (
        missing.get("selection_group") != orphan.get("disjoint_from")
        or orphan.get("selection_group") != missing.get("disjoint_from")
        or orphan.get("rate_pct") != 1.0
        or orphan.get("namespace_base") != 900000000
        or orphan.get("id_formula") != "namespace_base + order_id"
    ):
        raise ValueError("Logistics NULL/orphan selection policy is inconsistent")
    _require_field(config, missing["table"], missing["field"])
    _require_field(config, orphan["table"], orphan["field"])
    outlier = targets["order_total_outliers"]
    field = _require_field(config, outlier["table"], outlier["field"])
    clean_maximum = config["distributions"]["order_total"]["max_amount"]
    if (
        outlier.get("scale") != field.get("scale")
        or outlier["minimum_value"] <= clean_maximum
        or outlier["maximum_value"] < outlier["minimum_value"]
    ):
        raise ValueError("Logistics order-total outlier range is invalid")
    boundary = targets["coordinated_boundary_dates"]
    _require_qualified_field(config, boundary["primary_target"])
    for qualified in boundary["dependent_targets"]:
        _require_qualified_field(config, qualified)
    if not all(
        boundary.get(name) is True
        for name in ("preserve_chronology", "preserve_status_date_rules")
    ):
        raise ValueError("Logistics boundary rules must preserve consistency")
    for name, target in targets.items():
        reference = target.get("rate_source") or target.get("values_source")
        if reference:
            _resolve_reference(reference, base_config, config, name)


def _validate_join_paths(config: dict[str, Any]) -> None:
    """Validate stable join IDs, tables, conditions, and result requirements."""

    paths = config["join_path_requirements"]
    if [path.get("id") for path in paths] != EXPECTED_JOIN_PATH_IDS:
        raise ValueError("Logistics join path IDs differ from contract")
    valid_results = {"non_empty", "non_empty_with_unmatched_parent_rows"}
    for path in paths:
        path_tables = path.get("tables")
        if not isinstance(path_tables, list) or len(path_tables) < 2:
            raise ValueError(f"{path['id']} must contain at least two tables")
        if any(table_name not in config["tables"] for table_name in path_tables):
            raise ValueError(f"{path['id']} references an unknown table")
        if path.get("required_result") not in valid_results:
            raise ValueError(f"{path['id']} has an invalid result requirement")
        if path["required_result"].endswith("unmatched_parent_rows") and not path.get(
            "unmatched_condition"
        ):
            raise ValueError(f"{path['id']} lacks an unmatched condition")
        _validate_condition(config, path["id"], path["join_condition"], path_tables)
        if path.get("unmatched_condition"):
            _validate_condition(
                config, path["id"], path["unmatched_condition"], path_tables
            )


def _validate_decimal_and_currency_policy(config: dict[str, Any]) -> None:
    """Validate fixed-point metadata and executable USD constraints."""

    policy = config["decimal_policy"]
    precision = config["precision_metadata"]
    if (
        policy.get("rounding_mode") != "ROUND_HALF_UP"
        or policy.get("calculation_order") != "calculate_first_round_once"
        or policy.get("binary_float_prohibited") is not True
        or policy.get("stored_scale") != 2
        or policy.get("currency") != "USD"
        or precision.get("scale") != 2
        or precision.get("rounding_mode") != policy["rounding_mode"]
        or precision.get("calculation_order") != policy["calculation_order"]
    ):
        raise ValueError("Logistics decimal policy is inconsistent")
    expected_precision = {
        ("carriers", "base_rate"): precision["carrier_rate_precision"],
        ("warehouses", "utilization_pct"): precision["utilization_precision"],
        ("orders", "total_amount"): precision["order_amount_precision"],
        ("shipments", "shipping_cost"): precision["shipment_cost_precision"],
    }
    for (table_name, field_name), expected in expected_precision.items():
        field = _require_field(config, table_name, field_name)
        if (field.get("precision"), field.get("scale")) != (expected, 2):
            raise ValueError(f"{table_name}.{field_name} precision differs")
    constraints = ddl_constraints(PROJECT_ROOT / config["schema_source"])
    for table_name in USD_TABLES:
        field = _require_field(config, table_name, "currency_code")
        if field.get("default") != "USD" or field["nullable"]:
            raise ValueError(f"{table_name}.currency_code must be required USD")
        if not any(
            "currency_code='usd'" in check
            for check in constraints["checks"].get(table_name, set())
        ):
            raise ValueError(f"{table_name} DDL does not enforce USD")


def _validate_explicit_references(
    base_config: dict[str, Any], config: dict[str, Any]
) -> None:
    """Resolve every explicit shared or dotted domain reference."""

    for path, reference in _explicit_references(config):
        _resolve_reference(reference, base_config, config, path)


def _explicit_references(value: Any, path: str = "") -> list[tuple[str, str]]:
    """Collect explicit references without treating strategy names as paths."""

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
    """Resolve a dotted reference against shared or Logistics config."""

    if not isinstance(reference, str) or not reference:
        raise ValueError(f"{context} has an invalid config reference")
    if ":" in reference:
        source, dotted_path = reference.split(":", 1)
        if Path(source).name != "base.json":
            raise ValueError(f"{context} references unsupported config: {source}")
        current: Any = base_config
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
    """Return one configured Logistics field or raise a contextual error."""

    if table_name not in config["tables"]:
        raise ValueError(f"Unknown Logistics table: {table_name}")
    for field in config["tables"][table_name]["fields"]:
        if field["name"] == field_name:
            return field
    raise ValueError(f"Unknown Logistics field: {table_name}.{field_name}")


def _require_qualified_field(config: dict[str, Any], value: Any) -> None:
    """Validate one Logistics `table.field` reference."""

    if not isinstance(value, str) or value.count(".") != 1:
        raise ValueError(f"Invalid Logistics field reference: {value}")
    _require_field(config, *value.split("."))


def _validate_condition(
    config: dict[str, Any], path_id: str, condition: Any, path_tables: list[str]
) -> None:
    """Validate qualified fields in one configured join condition."""

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


def _validate_weights(mapping: Any, values: list[str]) -> None:
    """Validate complete non-negative weights that sum to one."""

    if not isinstance(mapping, dict) or set(mapping) != set(values):
        raise ValueError("Logistics weights must cover configured values")
    if (
        any(
            not isinstance(weight, (int, float))
            or isinstance(weight, bool)
            or weight < 0
            for weight in mapping.values()
        )
        or abs(sum(mapping.values()) - 1.0) > 1e-9
    ):
        raise ValueError("Logistics weights are invalid")


def _validate_probability(value: Any, path: str) -> None:
    """Validate a probability in the inclusive zero-to-one range."""

    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not 0 <= value <= 1
    ):
        raise ValueError(f"{path} must be between 0 and 1")


def _positive_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


__all__ = ["validate_logistics_config"]
