from __future__ import annotations

from pathlib import Path

from generators.core.base import DEFAULT_CONFIG_ROOT
from generators.core.base import GenerationSettings
from generators.core.config import load_domain_config
from generators.core.config import load_json_object
from generators.core.config import validate_domain_config_descriptor
from generators.core.schema_contract import configured_foreign_keys
from generators.core.schema_contract import configured_sql_type
from generators.core.schema_contract import configured_unique_constraints
from generators.core.schema_contract import ddl_constraints
from generators.core.schema_contract import ddl_table_specs
from generators.core.schema_contract import header_table_specs
from generators.core.schema_contract import normalized_default


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FINANCE_CONFIG_PATH = DEFAULT_CONFIG_ROOT / "finance.json"
FINANCE_HEADER_PATH = PROJECT_ROOT / "schemas/finance/finance_csv_header_spec.md"
TABLE_ORDER = [
    "accounts",
    "transactions",
    "ledger_entries",
    "budgets",
    "fx_rates",
]
EXPECTED_COMPONENT_SECTIONS = {
    "release": (
        "domain",
        "dataset_version",
        "schema_source",
        "fixed_values",
        "output_paths",
        "release_rules",
        "table_order",
        "precision_metadata",
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
        "decimal_policy",
        "distributions",
        "distribution_targets",
        "imperfection_targets",
    ),
    "validation": (
        "join_path_requirements",
        "accounting_rules",
        "conversion_rules",
        "consistency_rules",
    ),
}


def test_finance_descriptor_has_only_entry_point_metadata() -> None:
    assert load_json_object(FINANCE_CONFIG_PATH) == {
        "config_schema_version": "1.0",
        "domain": "finance",
        "components": {
            "release": "finance/release.json",
            "schema": "finance/schema.json",
            "generation": "finance/generation.json",
            "validation": "finance/validation.json",
        },
    }


def test_finance_components_own_expected_sections() -> None:
    descriptor = load_json_object(FINANCE_CONFIG_PATH)
    component_paths = validate_domain_config_descriptor(
        descriptor,
        FINANCE_CONFIG_PATH,
        config_root=DEFAULT_CONFIG_ROOT,
    )
    actual_sections = {
        component_name: tuple(load_json_object(path))
        for component_name, path in component_paths.items()
    }

    assert actual_sections == EXPECTED_COMPONENT_SECTIONS
    all_sections = [
        section for sections in actual_sections.values() for section in sections
    ]
    assert len(all_sections) == len(set(all_sections))


def test_assembled_finance_config_has_frozen_table_and_row_targets() -> None:
    config = _finance_config()

    assert config["domain"] == "finance"
    assert config["dataset_version"] == "dataset-v1.0.0"
    assert config["table_order"] == TABLE_ORDER
    assert {
        table_name: config["tables"][table_name]["row_targets"]
        for table_name in TABLE_ORDER
    } == {
        "accounts": {"dev": 50, "full": 200},
        "transactions": {"dev": 1000, "full": 100000},
        "ledger_entries": {"dev": 2000, "full": 200000},
        "budgets": {"dev": 100, "full": 800},
        "fx_rates": {"dev": 370, "full": 3650},
    }


def test_finance_settings_load_for_dev_and_full_profiles() -> None:
    dev = GenerationSettings.from_config_files("finance", "dev")
    full = GenerationSettings.from_config_files("finance", "full")

    assert dev.output_path == Path("tmp/generated/finance/dev").resolve()
    assert not dev.is_release_profile
    assert not dev.targets_versioned_release
    assert full.output_path == Path("release/finance/dataset-v1.0.0").resolve()
    assert full.is_release_profile
    assert full.targets_versioned_release
    assert dev.table_order == tuple(TABLE_ORDER)
    assert full.row_counts["ledger_entries"] == 200000


def test_finance_schema_config_matches_ddl_and_signed_headers() -> None:
    config = _finance_config()
    ddl_path = PROJECT_ROOT / config["schema_source"]
    ddl_tables = ddl_table_specs(ddl_path)
    header_tables = header_table_specs(FINANCE_HEADER_PATH)

    assert list(ddl_tables) == TABLE_ORDER
    assert list(header_tables) == TABLE_ORDER
    for table_name in TABLE_ORDER:
        configured_fields = config["tables"][table_name]["fields"]
        configured_names = [field["name"] for field in configured_fields]
        assert configured_names == list(ddl_tables[table_name])
        assert configured_names == [
            field["name"] for field in header_tables[table_name]
        ]
        for field in configured_fields:
            ddl_field = ddl_tables[table_name][field["name"]]
            assert configured_sql_type(field).replace(" ", "") == ddl_field["type"]
            assert field["nullable"] == ddl_field["nullable"]
            assert normalized_default(field.get("default")) == ddl_field["default"]


def test_finance_configured_keys_match_executable_ddl() -> None:
    config = _finance_config()
    constraints = ddl_constraints(PROJECT_ROOT / config["schema_source"])

    assert constraints["primary_keys"] == {
        table_name: (config["tables"][table_name]["primary_key"],)
        for table_name in TABLE_ORDER
    }
    assert configured_foreign_keys(config) == constraints["foreign_keys"]
    assert configured_unique_constraints(config) == constraints["unique_constraints"]


def test_finance_generation_contract_resolves_shared_presets() -> None:
    settings = GenerationSettings.from_config_files("finance", "dev")

    assert settings.distributions["transaction_amount"] == {
        "name": "pareto",
        "alpha": 1.16,
        "min_amount": 10,
        "max_amount": 10000000,
        "scale": 4,
    }
    assert settings.distributions["ledger_line_frequency"] == {
        "name": "poisson",
        "lambda": 3.2,
    }
    assert settings.distributions["date_clustering"]["name"] == "gaussian_mixture"
    assert settings.distributions["budget_amount"]["scale"] == 4


def test_finance_currency_pairs_and_fx_grid_reconcile_to_row_targets() -> None:
    config = _finance_config()
    rules = config["generation_rules"]
    pairs = rules["fx_calendar"]["currency_pairs"]
    anchors = config["business_mappings"]["fx_anchor_rates"]

    assert len(pairs) == 10
    assert set(pairs) == set(anchors)
    assert anchors["USD/USD"] == "1.000000"
    for profile in ("dev", "full"):
        date_count = rules["fx_calendar"]["expected_total_dates"][profile]
        row_target = config["tables"]["fx_rates"]["row_targets"][profile]
        assert len(pairs) * date_count == row_target
        assert (
            rules["fx_calendar"]["regular_date_counts"][profile]
            + rules["fx_calendar"]["boundary_date_count"]
            == date_count
        )


def test_finance_validation_and_imperfection_contracts_are_complete() -> None:
    config = _finance_config()

    assert [requirement["id"] for requirement in config["join_path_requirements"]] == [
        f"finance_jp_{index:03d}" for index in range(1, 11)
    ]
    assert set(config["imperfection_targets"]) == {
        "missing_fx_rates",
        "near_duplicate_budgets",
        "transaction_amount_outliers",
        "transaction_boundary_dates",
    }
    assert config["imperfection_targets"]["missing_fx_rates"]["protected_pair"] == (
        "USD/USD"
    )
    assert config["decimal_policy"] == {
        "money_precision": 19,
        "money_scale": 4,
        "fx_precision": 19,
        "fx_scale": 6,
        "rounding_mode": "ROUND_HALF_UP",
        "conversion_order": "multiply_first_round_once",
        "binary_float_prohibited": True,
    }


def _finance_config() -> dict[str, object]:
    """Load the assembled Finance component config for contract assertions."""

    return load_domain_config(FINANCE_CONFIG_PATH, config_root=DEFAULT_CONFIG_ROOT)
