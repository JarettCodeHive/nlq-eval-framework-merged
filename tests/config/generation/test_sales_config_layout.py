from __future__ import annotations

from pathlib import Path

from generators.core.base import DEFAULT_CONFIG_ROOT
from generators.core.base import GenerationSettings
from generators.core.config import load_domain_config
from generators.core.config import load_json_object
from generators.core.config import validate_domain_config_descriptor


SALES_CONFIG_PATH = DEFAULT_CONFIG_ROOT / "sales.json"
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
TABLE_ORDER = ["leads", "deals", "products", "quotations", "targets"]


def test_sales_descriptor_has_only_entry_point_metadata() -> None:
    assert load_json_object(SALES_CONFIG_PATH) == {
        "config_schema_version": "1.0",
        "domain": "sales",
        "components": {
            "release": "sales/release.json",
            "schema": "sales/schema.json",
            "generation": "sales/generation.json",
            "validation": "sales/validation.json",
        },
    }


def test_sales_components_own_expected_sections() -> None:
    descriptor = load_json_object(SALES_CONFIG_PATH)
    component_paths = validate_domain_config_descriptor(
        descriptor,
        SALES_CONFIG_PATH,
        config_root=DEFAULT_CONFIG_ROOT,
    )
    actual_sections = {
        component_name: tuple(load_json_object(path))
        for component_name, path in component_paths.items()
    }

    assert actual_sections == EXPECTED_COMPONENT_SECTIONS
    all_sections = [section for sections in actual_sections.values() for section in sections]
    assert len(all_sections) == len(set(all_sections))


def test_assembled_sales_config_has_frozen_table_and_row_targets() -> None:
    config = load_domain_config(SALES_CONFIG_PATH, config_root=DEFAULT_CONFIG_ROOT)

    assert config["domain"] == "sales"
    assert config["dataset_version"] == "dataset-v1.0.0"
    assert config["table_order"] == TABLE_ORDER
    assert {
        table_name: config["tables"][table_name]["row_targets"]
        for table_name in TABLE_ORDER
    } == {
        "leads": {"dev": 150, "full": 15000},
        "deals": {"dev": 50, "full": 5000},
        "products": {"dev": 50, "full": 500},
        "quotations": {"dev": 300, "full": 30000},
        "targets": {"dev": 40, "full": 400},
    }


def test_sales_settings_load_for_dev_and_full_profiles() -> None:
    dev = GenerationSettings.from_config_files("sales", "dev")
    full = GenerationSettings.from_config_files("sales", "full")

    assert dev.output_path == Path("tmp/generated/sales/dev").resolve()
    assert not dev.is_release_profile
    assert not dev.targets_versioned_release
    assert full.output_path == Path("release/sales/dataset-v1.0.0").resolve()
    assert full.is_release_profile
    assert full.targets_versioned_release
    assert dev.table_order == tuple(TABLE_ORDER)
    assert full.row_counts["quotations"] == 30000


def test_sales_generation_contract_selects_shared_distribution_presets() -> None:
    settings = GenerationSettings.from_config_files("sales", "dev")

    assert settings.distributions["deal_amount"]["name"] == "pareto"
    assert settings.distributions["quotation_frequency"] == {
        "name": "poisson",
        "lambda": 6.0,
    }
    assert settings.distributions["date_clustering"]["name"] == "gaussian_mixture"
    assert settings.distributions["quota_amount"] == {
        "name": "pareto",
        "alpha": 1.35,
        "min_amount": 100000,
        "max_amount": 5000000,
        "scale": 2,
    }
