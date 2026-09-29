from __future__ import annotations

from pathlib import Path

from generators.core.config import load_domain_config


ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = ROOT / "config" / "generation" / "logistics.json"


def test_logistics_component_descriptor_assembles_expected_sections() -> None:
    config = load_domain_config(CONFIG_PATH)

    assert list(config) == [
        "domain",
        "dataset_version",
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
    ]
    assert config["domain"] == "logistics"
    assert config["table_order"] == [
        "carriers",
        "warehouses",
        "orders",
        "shipments",
        "inventory",
    ]


def test_logistics_config_freezes_targets_and_semantic_values() -> None:
    config = load_domain_config(CONFIG_PATH)

    assert {
        table_name: table["row_targets"]
        for table_name, table in config["tables"].items()
    } == {
        "carriers": {"dev": 10, "full": 50},
        "warehouses": {"dev": 10, "full": 100},
        "orders": {"dev": 1000, "full": 100000},
        "shipments": {"dev": 1500, "full": 150000},
        "inventory": {"dev": 500, "full": 50000},
    }
    orphan = config["imperfection_targets"]["orphaned_order_warehouses"]
    assert orphan["rate_pct"] == 1.0
    assert orphan["namespace_base"] == 900000000
    assert config["business_mappings"]["currency_code"] == "USD"
    assert config["decimal_policy"]["rounding_mode"] == "ROUND_HALF_UP"
    assert [item["id"] for item in config["join_path_requirements"]] == [
        f"logistics_jp_{number:03d}" for number in range(1, 7)
    ]
