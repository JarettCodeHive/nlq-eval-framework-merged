from __future__ import annotations

from generators.core.base import DEFAULT_CONFIG_ROOT
from generators.core.config import load_json_object
from generators.core.config import validate_domain_config_descriptor
from generators.crm.config import CRM_CONFIG_PATH


EXPECTED_COMPONENT_SECTIONS = {
    "release": (
        "domain",
        "dataset_version",
        "schema_source",
        "fixed_values",
        "output_paths",
        "release_rules",
        "table_order",
        "generation_notes",
    ),
    "schema": (
        "tables",
        "relationships",
    ),
    "generation": (
        "domain_values",
        "generation_rules",
        "business_mappings",
        "distributions",
        "distribution_targets",
        "imperfection_targets",
    ),
    "validation": (
        "join_path_requirements",
        "consistency_rules",
    ),
}


def test_live_crm_descriptor_has_only_entry_point_metadata() -> None:
    descriptor = load_json_object(CRM_CONFIG_PATH)

    assert descriptor == {
        "config_schema_version": "1.0",
        "domain": "crm",
        "components": {
            "release": "crm/release.json",
            "schema": "crm/schema.json",
            "generation": "crm/generation.json",
            "validation": "crm/validation.json",
        },
    }


def test_live_crm_components_own_expected_sections() -> None:
    descriptor = load_json_object(CRM_CONFIG_PATH)
    component_paths = validate_domain_config_descriptor(
        descriptor,
        CRM_CONFIG_PATH,
        config_root=DEFAULT_CONFIG_ROOT,
    )

    actual_sections = {
        component_name: tuple(load_json_object(path))
        for component_name, path in component_paths.items()
    }

    assert actual_sections == EXPECTED_COMPONENT_SECTIONS
    all_sections = [
        section
        for sections in actual_sections.values()
        for section in sections
    ]
    assert len(all_sections) == len(set(all_sections))
