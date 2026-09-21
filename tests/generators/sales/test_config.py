from __future__ import annotations

from copy import deepcopy
import json

import pytest

import generators.sales.config as config_module
import generators.sales.validators.config as validation_module
from generators.sales.config import load_sales_config
from generators.sales.config import SALES_CONFIG_PATH
from generators.sales.config import settings_for_profile
from generators.sales.generator import SALES_COLUMN_CONTRACTS
from generators.sales.generator import build_sales_column_contracts
from generators.sales.validators.config import validate_sales_config


EXPECTED_COLUMNS = {
    "leads": [
        "lead_id",
        "lead_name",
        "company_name",
        "lead_source",
        "lead_status",
        "rep_name",
        "territory",
        "score",
        "created_at",
    ],
    "deals": [
        "deal_id",
        "lead_id",
        "deal_name",
        "rep_name",
        "stage",
        "deal_amount",
        "currency_code",
        "close_date",
        "expected_close_date",
        "created_at",
    ],
    "products": [
        "product_id",
        "sku",
        "product_name",
        "category",
        "list_price",
        "currency_code",
        "is_active",
        "created_at",
    ],
    "quotations": [
        "quotation_id",
        "deal_id",
        "product_id",
        "quote_number",
        "quantity",
        "unit_price",
        "discount_pct",
        "quote_status",
        "quoted_at",
        "created_at",
    ],
    "targets": [
        "target_id",
        "rep_name",
        "territory",
        "period_start",
        "period_end",
        "quota_amount",
        "currency_code",
        "created_at",
    ],
}

EXPECTED_COMPONENTS = {
    "release": "sales/release.json",
    "schema": "sales/schema.json",
    "generation": "sales/generation.json",
    "validation": "sales/validation.json",
}


def test_sales_config_validates_before_generation() -> None:
    validate_sales_config()


def test_sales_descriptor_declares_all_component_files() -> None:
    descriptor = json.loads(SALES_CONFIG_PATH.read_text(encoding="utf-8"))

    assert descriptor == {
        "config_schema_version": "1.0",
        "domain": "sales",
        "components": EXPECTED_COMPONENTS,
    }
    for relative_path in EXPECTED_COMPONENTS.values():
        assert (SALES_CONFIG_PATH.parent / relative_path).is_file()


def test_sales_loader_uses_shared_component_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {"domain": "sales"}
    captured: dict[str, object] = {}

    def load(config_path: object, config_root: object) -> dict[str, str]:
        captured["config_path"] = config_path
        captured["config_root"] = config_root
        return expected

    monkeypatch.setattr(config_module, "load_domain_config", load)

    assert config_module.load_sales_config() is expected
    assert captured == {
        "config_path": config_module.SALES_CONFIG_PATH,
        "config_root": config_module.DEFAULT_CONFIG_ROOT,
    }


def test_sales_settings_use_frozen_profiles_and_table_order() -> None:
    dev = settings_for_profile("dev")
    full = settings_for_profile("full")

    assert dev.table_order == tuple(EXPECTED_COLUMNS)
    assert dev.row_counts["quotations"] == 300
    assert full.row_counts["quotations"] == 30000
    assert full.targets_versioned_release


def test_sales_columns_match_frozen_schema() -> None:
    config = load_sales_config()

    assert {
        table_name: [field["name"] for field in config["tables"][table_name]["fields"]]
        for table_name in config["table_order"]
    } == EXPECTED_COLUMNS


def test_sales_generator_column_contracts_are_config_derived() -> None:
    assert SALES_COLUMN_CONTRACTS == EXPECTED_COLUMNS
    assert build_sales_column_contracts() == SALES_COLUMN_CONTRACTS


def test_column_contract_builder_tracks_config_without_static_column_lists() -> None:
    config = deepcopy(load_sales_config())
    config["tables"]["leads"]["fields"].append(
        {
            "name": "test_only_field",
            "type": "varchar",
            "max_length": 32,
            "nullable": True,
        }
    )

    contracts = build_sales_column_contracts(config)

    assert contracts["leads"][-1] == "test_only_field"
    assert contracts["leads"][:-1] == EXPECTED_COLUMNS["leads"]


def test_column_contract_builder_rejects_duplicate_fields() -> None:
    config = deepcopy(load_sales_config())
    config["tables"]["leads"]["fields"].append(
        deepcopy(config["tables"]["leads"]["fields"][0])
    )

    with pytest.raises(ValueError, match="column contract contains duplicates"):
        build_sales_column_contracts(config)


def test_schema_alignment_rejects_field_drift() -> None:
    config = deepcopy(load_sales_config())
    config["tables"]["products"]["fields"].pop()

    with pytest.raises(ValueError, match="products fields differ"):
        validation_module._validate_schema_alignment(config)


def test_validation_rejects_duplicate_domain_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_sales_config())
    config["domain_values"]["product_categories"].append("Software")
    monkeypatch.setattr(config_module, "load_sales_config", lambda: config)

    with pytest.raises(ValueError, match="product_categories contains duplicates"):
        validate_sales_config()


def test_validation_rejects_weights_that_do_not_sum_to_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_sales_config())
    config["generation_rules"]["deals"]["stage_weights"]["Won"] = 0.1
    monkeypatch.setattr(config_module, "load_sales_config", lambda: config)

    with pytest.raises(ValueError, match="weights must sum to 1"):
        validate_sales_config()


def test_validation_rejects_target_capacity_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_sales_config())
    config["generation_rules"]["representatives"]["pool_sizes"]["dev"] = 9
    monkeypatch.setattr(config_module, "load_sales_config", lambda: config)

    with pytest.raises(ValueError, match="representative-period capacity"):
        validate_sales_config()


def test_validation_rejects_unknown_distribution_preset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_sales_config())
    config["distributions"]["deal_amount"]["preset"] = "missing"
    monkeypatch.setattr(config_module, "load_sales_config", lambda: config)

    with pytest.raises(ValueError, match="unknown distribution preset"):
        validate_sales_config()


def test_validation_rejects_non_nullable_missing_value_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_sales_config())
    config["imperfection_targets"]["missing_product_prices"] = {
        "table": "deals",
        "field": "deal_amount",
        "rate_source": "config/generation/base.json:imperfections.null_pct",
    }
    monkeypatch.setattr(config_module, "load_sales_config", lambda: config)

    with pytest.raises(ValueError, match="missing-value target must be nullable"):
        validate_sales_config()


def test_validation_accounts_for_final_quotation_duplicate_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_sales_config())
    config["tables"]["quotations"]["row_targets"]["full"] = 250000
    monkeypatch.setattr(config_module, "load_sales_config", lambda: config)

    with pytest.raises(ValueError, match="final quotations exceed row cap"):
        validate_sales_config()


def test_validation_rejects_join_path_id_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_sales_config())
    config["join_path_requirements"][0]["id"] = "sales_jp_999"
    monkeypatch.setattr(config_module, "load_sales_config", lambda: config)

    with pytest.raises(ValueError, match="Sales join path IDs differ"):
        validate_sales_config()


def test_validation_rejects_non_usd_business_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_sales_config())
    config["business_mappings"]["currency_code"] = "EUR"
    monkeypatch.setattr(config_module, "load_sales_config", lambda: config)

    with pytest.raises(ValueError, match="business currency must be USD"):
        validate_sales_config()


def test_quota_relationship_is_analytical_not_a_foreign_key() -> None:
    config = load_sales_config()
    relationship = config["relationships"][-1]

    assert relationship["relationship_type"] == "analytical"
    assert relationship["enforced_as_foreign_key"] is False
    assert "period_start" in relationship["additional_condition"]
    assert "period_end" in relationship["additional_condition"]
