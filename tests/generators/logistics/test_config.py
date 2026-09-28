from __future__ import annotations

from copy import deepcopy

import pytest

import generators.logistics.config as config_module
from generators.logistics.config import load_logistics_config
from generators.logistics.config import LOGISTICS_CONFIG_PATH
from generators.logistics.config import settings_for_profile
from generators.logistics.validators.config import validate_logistics_config


def _validate_with(
    monkeypatch: pytest.MonkeyPatch,
    config: dict[str, object],
) -> None:
    monkeypatch.setattr(config_module, "load_logistics_config", lambda: config)
    validate_logistics_config()


def test_logistics_config_validates_before_generation() -> None:
    validate_logistics_config()


def test_logistics_loader_uses_shared_component_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {"domain": "logistics"}
    captured: dict[str, object] = {}

    def load(config_path: object, config_root: object) -> dict[str, str]:
        captured["config_path"] = config_path
        captured["config_root"] = config_root
        return expected

    monkeypatch.setattr(config_module, "load_domain_config", load)

    assert config_module.load_logistics_config() is expected
    assert captured == {
        "config_path": LOGISTICS_CONFIG_PATH,
        "config_root": config_module.DEFAULT_CONFIG_ROOT,
    }


def test_logistics_settings_resolve_frozen_profiles() -> None:
    dev = settings_for_profile("dev")
    full = settings_for_profile("full")

    assert dev.table_order == (
        "carriers",
        "warehouses",
        "orders",
        "shipments",
        "inventory",
    )
    assert dev.row_counts["shipments"] == 1500
    assert full.row_counts["shipments"] == 150000
    assert full.targets_versioned_release


def test_validation_rejects_missing_required_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_logistics_config())
    config.pop("orphan_exclusions")

    with pytest.raises(ValueError, match="missing sections"):
        _validate_with(monkeypatch, config)


def test_validation_rejects_broken_dotted_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_logistics_config())
    config["tables"]["carriers"]["fields"][1]["synthetic_source"] = (
        "business_mappings.synthetic_names.missing"
    )

    with pytest.raises(ValueError, match="references missing config value"):
        _validate_with(monkeypatch, config)


def test_validation_rejects_orphan_policy_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = deepcopy(load_logistics_config())
    config["imperfection_targets"]["orphaned_order_warehouses"][
        "namespace_base"
    ] = 100

    with pytest.raises(ValueError, match="NULL/orphan selection policy"):
        _validate_with(monkeypatch, config)
