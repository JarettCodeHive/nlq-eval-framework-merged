from __future__ import annotations

from copy import deepcopy

import pytest

import generators.finance.config as config_module
import generators.finance.validators.config as validation_module
from generators.core.base import resolve_distribution_settings
from generators.finance.config import FINANCE_CONFIG_PATH
from generators.finance.config import load_finance_config
from generators.finance.config import settings_for_profile
from generators.finance.generator import FINANCE_COLUMN_CONTRACTS
from generators.finance.generator import build_finance_column_contracts
from generators.finance.validators.config import validate_finance_config


EXPECTED_TABLE_ORDER = (
    "accounts",
    "transactions",
    "ledger_entries",
    "budgets",
    "fx_rates",
)
EXPECTED_COLUMNS = {
    "accounts": [
        "account_id",
        "account_number",
        "account_name",
        "account_type",
        "account_subtype",
        "currency_code",
        "parent_account_id",
        "normal_balance",
        "is_active",
        "created_at",
    ],
    "transactions": [
        "transaction_id",
        "transaction_date",
        "description",
        "source_system",
        "source_currency",
        "target_currency",
        "total_amount",
        "reversed",
        "posted_at",
    ],
    "ledger_entries": [
        "entry_id",
        "transaction_id",
        "account_id",
        "line_number",
        "debit_amount",
        "credit_amount",
        "currency_code",
        "posting_type",
        "created_at",
    ],
    "budgets": [
        "budget_id",
        "account_id",
        "fiscal_year",
        "period_start",
        "period_end",
        "budget_amount",
        "currency_code",
        "scenario",
        "created_at",
    ],
    "fx_rates": [
        "rate_id",
        "from_currency",
        "to_currency",
        "rate_date",
        "rate",
        "rate_source",
        "is_estimated",
        "created_at",
    ],
}


def test_finance_config_validates_before_generation() -> None:
    validate_finance_config()


def test_finance_loader_uses_shared_component_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {"domain": "finance"}
    captured: dict[str, object] = {}

    def load(config_path: object, config_root: object) -> dict[str, str]:
        captured["config_path"] = config_path
        captured["config_root"] = config_root
        return expected

    monkeypatch.setattr(config_module, "load_domain_config", load)

    assert config_module.load_finance_config() is expected
    assert captured == {
        "config_path": FINANCE_CONFIG_PATH,
        "config_root": config_module.DEFAULT_CONFIG_ROOT,
    }


def test_finance_settings_use_frozen_profiles_and_table_order() -> None:
    dev = settings_for_profile("dev")
    full = settings_for_profile("full")

    assert dev.table_order == EXPECTED_TABLE_ORDER
    assert dev.row_counts == {
        "accounts": 50,
        "transactions": 1000,
        "ledger_entries": 2000,
        "budgets": 100,
        "fx_rates": 370,
    }
    assert full.row_counts["ledger_entries"] == 200000
    assert full.targets_versioned_release


def test_finance_column_contracts_are_derived_from_schema_config() -> None:
    config = load_finance_config()

    assert FINANCE_COLUMN_CONTRACTS == EXPECTED_COLUMNS
    assert build_finance_column_contracts() == FINANCE_COLUMN_CONTRACTS
    assert {
        table_name: [field["name"] for field in config["tables"][table_name]["fields"]]
        for table_name in config["table_order"]
    } == FINANCE_COLUMN_CONTRACTS


def test_finance_column_contract_builder_tracks_config_changes() -> None:
    config = deepcopy(load_finance_config())
    config["tables"]["accounts"]["fields"].append(
        {
            "name": "test_only_field",
            "type": "varchar",
            "max_length": 32,
            "nullable": True,
        }
    )

    contracts = build_finance_column_contracts(config)

    assert contracts["accounts"][-1] == "test_only_field"
    assert contracts["accounts"][:-1] == EXPECTED_COLUMNS["accounts"]


def test_finance_column_contract_builder_rejects_duplicate_fields() -> None:
    config = deepcopy(load_finance_config())
    config["tables"]["accounts"]["fields"].append(
        deepcopy(config["tables"]["accounts"]["fields"][0])
    )

    with pytest.raises(ValueError, match="column contract contains duplicates"):
        build_finance_column_contracts(config)


def test_validation_rejects_missing_required_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_finance_config())
    config.pop("decimal_policy")
    monkeypatch.setattr(config_module, "load_finance_config", lambda: config)

    with pytest.raises(ValueError, match="missing sections"):
        validate_finance_config()


def test_validation_rejects_schema_field_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_finance_config())
    config["tables"]["fx_rates"]["fields"].pop()
    monkeypatch.setattr(config_module, "load_finance_config", lambda: config)

    with pytest.raises(ValueError, match="fx_rates fields differ"):
        validate_finance_config()


def test_validation_rejects_composite_unique_key_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_finance_config())
    config["tables"]["fx_rates"]["unique_constraints"] = [
        ["from_currency", "rate_date"]
    ]
    monkeypatch.setattr(config_module, "load_finance_config", lambda: config)

    with pytest.raises(ValueError, match="unique constraints differ"):
        validate_finance_config()


def test_validation_rejects_physical_fx_relationship(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_finance_config())
    config["relationships"][-1]["enforced_as_foreign_key"] = True
    monkeypatch.setattr(config_module, "load_finance_config", lambda: config)

    with pytest.raises(ValueError, match="cannot be a physical FK"):
        validate_finance_config()


def test_validation_rejects_incomplete_account_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_finance_config())
    config["business_mappings"]["account_subtypes"].pop("Expense")
    monkeypatch.setattr(config_module, "load_finance_config", lambda: config)

    with pytest.raises(ValueError, match="subtype mapping is incomplete"):
        validate_finance_config()


def test_validation_rejects_non_identity_usd_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_finance_config())
    config["business_mappings"]["fx_anchor_rates"]["USD/USD"] = "1.010000"
    monkeypatch.setattr(config_module, "load_finance_config", lambda: config)

    with pytest.raises(ValueError, match="USD/USD anchor"):
        validate_finance_config()


def test_validation_rejects_fx_grid_capacity_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_finance_config())
    config["generation_rules"]["fx_calendar"]["regular_date_counts"]["dev"] = 32
    monkeypatch.setattr(config_module, "load_finance_config", lambda: config)

    with pytest.raises(ValueError, match="date counts do not reconcile"):
        validate_finance_config()


def test_validation_rejects_unknown_distribution_preset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_finance_config())
    config["distributions"]["transaction_amount"]["preset"] = "missing"
    monkeypatch.setattr(config_module, "load_finance_config", lambda: config)

    with pytest.raises(ValueError, match="unknown distribution preset"):
        validate_finance_config()


def test_validation_rejects_non_nullable_missing_rate_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_finance_config())
    config["imperfection_targets"]["missing_fx_rates"].update(
        {"table": "transactions", "field": "total_amount"}
    )
    monkeypatch.setattr(config_module, "load_finance_config", lambda: config)

    with pytest.raises(ValueError, match="missing FX-rate target"):
        validate_finance_config()


def test_validation_rejects_outliers_without_ledger_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_finance_config())
    config["imperfection_targets"]["transaction_amount_outliers"][
        "dependent_rebuild"
    ] = "none"
    monkeypatch.setattr(config_module, "load_finance_config", lambda: config)

    with pytest.raises(ValueError, match="must rebuild dependent ledger"):
        validate_finance_config()


def test_validation_accounts_for_final_budget_duplicate_rows() -> None:
    config = load_finance_config()
    base = config_module.load_base_config()
    base["max_rows_per_table"] = 807

    with pytest.raises(ValueError, match="final budgets exceed row cap"):
        validation_module._validate_profile_capacities(base, config)


def test_validation_rejects_insufficient_ledger_capacity() -> None:
    config = deepcopy(load_finance_config())
    config["tables"]["ledger_entries"]["row_targets"]["dev"] = 1898

    with pytest.raises(ValueError, match="ledger target lacks capacity"):
        validation_module._validate_profile_capacities(
            config_module.load_base_config(), config
        )


def test_validation_rejects_join_path_id_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_finance_config())
    config["join_path_requirements"][0]["id"] = "finance_jp_999"
    monkeypatch.setattr(config_module, "load_finance_config", lambda: config)

    with pytest.raises(ValueError, match="Finance join path IDs differ"):
        validate_finance_config()


def test_validation_rejects_decimal_policy_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_finance_config())
    config["decimal_policy"]["rounding_mode"] = "ROUND_HALF_EVEN"
    monkeypatch.setattr(config_module, "load_finance_config", lambda: config)

    with pytest.raises(ValueError, match="decimal policy differs"):
        validate_finance_config()


def test_effective_finance_distributions_preserve_required_scales() -> None:
    config = load_finance_config()
    resolved = resolve_distribution_settings(
        config_module.load_base_config()["distribution_defaults"],
        config["distributions"],
    )

    assert resolved["transaction_amount"]["scale"] == 4
    assert resolved["budget_amount"]["scale"] == 4
    assert resolved["ledger_line_frequency"]["name"] == "poisson"
    assert resolved["date_clustering"]["name"] == "gaussian_mixture"
