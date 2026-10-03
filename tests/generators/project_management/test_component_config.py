from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any

import pytest

from generators.core.base import GenerationSettings
from generators.core.base import PROJECT_ROOT
from generators.core.config import load_domain_config
from generators.core.schema_contract import configured_foreign_keys
from generators.core.schema_contract import configured_sql_type
from generators.core.schema_contract import ddl_constraints
from generators.core.schema_contract import ddl_table_specs
from generators.core.schema_contract import header_table_specs
from generators.core.schema_contract import normalized_default
from generators.core.schema_contract import primary_key_fields


CONFIG_ROOT = PROJECT_ROOT / "config" / "generation"
DESCRIPTOR = CONFIG_ROOT / "project_management.json"
DDL = PROJECT_ROOT / "schemas" / "project_management" / "project_management_ddl.sql"
HEADER = (
    PROJECT_ROOT
    / "schemas"
    / "project_management"
    / "project_management_csv_header_spec.md"
)
TABLE_ORDER = [
    "projects",
    "resources",
    "tasks",
    "task_resources",
    "milestones",
    "time_entries",
]
ROW_TARGETS = {
    "dev": {
        "projects": 10,
        "resources": 10,
        "tasks": 100,
        "task_resources": 300,
        "milestones": 25,
        "time_entries": 1500,
    },
    "full": {
        "projects": 500,
        "resources": 100,
        "tasks": 10000,
        "task_resources": 30000,
        "milestones": 2500,
        "time_entries": 150000,
    },
}


def _config() -> dict[str, Any]:
    return load_domain_config(DESCRIPTOR, config_root=CONFIG_ROOT)


def test_descriptor_composes_four_non_overlapping_components() -> None:
    descriptor = json.loads(DESCRIPTOR.read_text(encoding="utf-8"))
    config = _config()

    assert descriptor == {
        "config_schema_version": "1.0",
        "domain": "project_management",
        "components": {
            "release": "project_management/release.json",
            "schema": "project_management/schema.json",
            "generation": "project_management/generation.json",
            "validation": "project_management/validation.json",
        },
    }
    assert config["domain"] == "project_management"
    assert config["table_order"] == TABLE_ORDER
    assert list(config["tables"]) == TABLE_ORDER
    assert len(config["join_path_requirements"]) == 10


@pytest.mark.parametrize("profile", ["dev", "full"])
def test_generation_settings_resolve_for_each_profile(profile: str) -> None:
    settings = GenerationSettings.from_config_files("project_management", profile)

    assert settings.domain == "project_management"
    assert settings.release_version == "v1.0.0"
    assert settings.reference_today.isoformat() == "2026-08-01"
    assert list(settings.table_order) == TABLE_ORDER
    assert settings.row_counts == ROW_TARGETS[profile]
    assert settings.schema_source == DDL
    assert settings.schema_source.is_file()
    assert settings.distributions["project_budget"]["name"] == "pareto"
    assert settings.distributions["time_entry_frequency"]["name"] == "poisson"
    assert settings.distributions["date_clustering"]["name"] == ("gaussian_mixture")
    if profile == "dev":
        assert settings.output_path == (
            PROJECT_ROOT / "tmp" / "generated" / "project_management" / "dev"
        )
        assert not settings.targets_versioned_release
    else:
        assert settings.output_path == (
            PROJECT_ROOT / "release" / "project_management" / "v1.0.0" / "dataset"
        )
        assert settings.targets_versioned_release


def test_schema_component_matches_ddl_and_header_contracts() -> None:
    config = _config()
    ddl = ddl_table_specs(DDL)
    headers = header_table_specs(HEADER)

    assert list(ddl) == TABLE_ORDER
    assert list(headers) == TABLE_ORDER
    for table_name in TABLE_ORDER:
        configured_fields = config["tables"][table_name]["fields"]
        assert [field["name"] for field in configured_fields] == list(ddl[table_name])
        assert [field["name"] for field in configured_fields] == [
            field["name"] for field in headers[table_name]
        ]
        for field in configured_fields:
            ddl_field = ddl[table_name][field["name"]]
            assert configured_sql_type(field) == ddl_field["type"]
            assert bool(field["nullable"]) == ddl_field["nullable"]
            assert normalized_default(field.get("default")) == ddl_field["default"]


def test_schema_component_matches_ddl_keys_and_relationships() -> None:
    config = _config()
    constraints = ddl_constraints(DDL)
    configured_primary_keys = {
        table_name: tuple(primary_key_fields(table_config))
        for table_name, table_config in config["tables"].items()
    }

    assert configured_primary_keys == constraints["primary_keys"]
    assert configured_foreign_keys(config) == constraints["foreign_keys"]
    semantic = [
        relationship
        for relationship in config["relationships"]
        if relationship["relationship_type"] == "semantic_composite_membership"
    ]
    assert semantic == [
        {
            "relationship_type": "semantic_composite_membership",
            "parent_table": "task_resources",
            "parent_fields": ["task_id", "resource_id"],
            "child_table": "time_entries",
            "child_fields": ["task_id", "resource_id"],
            "cardinality": "1:N",
            "join_type": "inner",
            "enforced_as_foreign_key": False,
        }
    ]


def test_all_explicit_dotted_config_references_resolve() -> None:
    config = _config()
    base = json.loads((CONFIG_ROOT / "base.json").read_text(encoding="utf-8"))
    references = list(_explicit_references(config))

    assert references
    for path, reference in references:
        assert (
            _resolve_reference(reference, config, base) is not None
        ), f"{path} does not resolve: {reference}"


def test_profile_capacities_support_required_cardinality() -> None:
    config = _config()

    for profile, targets in ROW_TARGETS.items():
        assert targets["task_resources"] >= targets["tasks"]
        assert targets["task_resources"] >= targets["resources"]
        assert targets["task_resources"] <= (targets["tasks"] * targets["resources"])
        assert targets["milestones"] >= targets["projects"]
        assert targets["time_entries"] >= targets["task_resources"]
        for table_name in TABLE_ORDER:
            assert (
                config["tables"][table_name]["row_targets"][profile]
                == (targets[table_name])
            )


def test_domain_values_are_config_owned_non_empty_and_unique() -> None:
    domain_values = _config()["domain_values"]

    assert domain_values
    for name, values in domain_values.items():
        assert isinstance(values, list), name
        assert values, name
        assert len(values) == len(set(values)), name
        assert all(isinstance(value, str) and value.isascii() for value in values), name


def _explicit_references(value: Any, path: str = "") -> list[tuple[str, str]]:
    references: list[tuple[str, str]] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if isinstance(child, str) and (
                (
                    key.endswith("_source")
                    and key not in {"schema_source", "synthetic_source"}
                )
                or (
                    key == "synthetic_source"
                    and child.split(".", maxsplit=1)[0]
                    in {
                        "business_mappings",
                        "distributions",
                        "domain_values",
                        "generation_rules",
                    }
                )
            ):
                references.append((child_path, child))
            references.extend(_explicit_references(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            references.extend(_explicit_references(child, f"{path}[{index}]"))
    return references


def _resolve_reference(
    reference: str,
    config: dict[str, Any],
    base: dict[str, Any],
) -> Any:
    if ":" in reference:
        source, dotted_path = reference.split(":", maxsplit=1)
        assert Path(source).name == "base.json"
        current: Any = base
    else:
        dotted_path = reference
        current = config
    for part in dotted_path.split("."):
        assert isinstance(current, Mapping) and part in current
        current = current[part]
    return current
