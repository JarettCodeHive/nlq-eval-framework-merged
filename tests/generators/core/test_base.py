from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from generators.core.base import DeterministicGenerator
from generators.core.base import DEFAULT_CONFIG_ROOT
from generators.core.base import GenerationSettings
from generators.core.base import validate_column_contracts
from generators.crm.config import load_crm_config


def test_settings_load_dev_profile_from_config() -> None:
    settings = GenerationSettings.from_config_files("crm", "dev")

    assert settings.domain == "crm"
    assert settings.dataset_version == "dataset-v1.0.0"
    assert settings.profile == "dev"
    assert settings.seed == 42
    assert settings.reference_today.isoformat() == "2026-08-01"
    assert settings.row_counts["accounts"] == 100
    assert settings.row_counts["interactions"] == 1000


def test_settings_load_full_profile_from_config() -> None:
    settings = GenerationSettings.from_config_files("crm", "full")

    assert settings.is_release_profile
    assert settings.row_counts["accounts"] == 24000
    assert settings.row_counts["interactions"] == 200000


def test_component_config_produces_same_settings_as_monolithic_config(
    tmp_path: Path,
) -> None:
    base_config = json.loads(
        (DEFAULT_CONFIG_ROOT / "base.json").read_text(encoding="utf-8")
    )
    crm_config = load_crm_config()
    components = {
        "release": {
            key: crm_config[key]
            for key in (
                "domain",
                "dataset_version",
                "schema_source",
                "fixed_values",
                "output_paths",
                "release_rules",
                "table_order",
                "generation_notes",
            )
        },
        "schema": {
            key: crm_config[key] for key in ("tables", "relationships")
        },
        "generation": {
            key: crm_config[key]
            for key in (
                "domain_values",
                "generation_rules",
                "business_mappings",
                "distributions",
                "distribution_targets",
                "imperfection_targets",
            )
        },
        "validation": {
            key: crm_config[key]
            for key in ("join_path_requirements", "consistency_rules")
        },
    }
    component_root = tmp_path / "crm"
    component_root.mkdir()
    _write_json(tmp_path / "base.json", base_config)
    for component_name, component in components.items():
        _write_json(component_root / f"{component_name}.json", component)
    _write_json(
        tmp_path / "crm.json",
        {
            "config_schema_version": "1.0",
            "domain": "crm",
            "components": {
                component_name: f"crm/{component_name}.json"
                for component_name in components
            },
        },
    )

    component_settings = GenerationSettings.from_config_files(
        "crm",
        "dev",
        config_root=tmp_path,
    )
    monolithic_settings = GenerationSettings.from_config_files("crm", "dev")

    assert component_settings.metadata() == monolithic_settings.metadata()
    assert component_settings.base_config_path == tmp_path / "base.json"
    assert component_settings.domain_config_path == tmp_path / "crm.json"


def test_child_seed_is_stable_across_instances() -> None:
    first = DeterministicGenerator.for_domain_profile("crm", "dev")
    second = DeterministicGenerator.for_domain_profile("crm", "dev")

    assert first.child_seed("accounts") == second.child_seed("accounts")
    assert first.child_seed("accounts") != first.child_seed("contacts")


def test_metadata_is_deterministic() -> None:
    generator = DeterministicGenerator.for_domain_profile("crm", "dev")

    assert generator.metadata() == generator.metadata()
    assert generator.metadata()["manifest_generated_at"] == "2026-09-04T00:00:00"


def test_column_contracts_match_configured_field_order() -> None:
    validate_column_contracts(
        ["example"],
        {"example": {"fields": [{"name": "id"}, {"name": "value"}]}},
        {"example": ["id", "value"]},
    )


def test_column_contracts_reject_field_order_mismatch() -> None:
    with pytest.raises(ValueError, match="generator columns differ from config"):
        validate_column_contracts(
            ["example"],
            {"example": {"fields": [{"name": "id"}, {"name": "value"}]}},
            {"example": ["value", "id"]},
        )


def test_make_integer_ids_uses_stable_order() -> None:
    generator = DeterministicGenerator.for_domain_profile("crm", "dev")

    assert generator.make_integer_ids(3) == [1, 2, 3]
    assert generator.make_integer_ids(3, start=10) == [10, 11, 12]


def test_unknown_profile_fails_clearly() -> None:
    with pytest.raises(ValueError, match="Unknown generation profile"):
        GenerationSettings.from_config_files("crm", "missing")


def test_rng_stream_is_stable_when_numpy_is_installed() -> None:
    if importlib.util.find_spec("numpy") is None:
        pytest.skip("numpy is not installed")

    first = DeterministicGenerator.for_domain_profile("crm", "dev")
    second = DeterministicGenerator.for_domain_profile("crm", "dev")

    first_values = first.rng_for("accounts").integers(0, 10000, size=5).tolist()
    second_values = second.rng_for("accounts").integers(0, 10000, size=5).tolist()

    assert first_values == second_values


def test_faker_stream_is_stable_when_faker_is_installed() -> None:
    if importlib.util.find_spec("faker") is None:
        pytest.skip("Faker is not installed")

    first = DeterministicGenerator.for_domain_profile("crm", "dev")
    second = DeterministicGenerator.for_domain_profile("crm", "dev")

    first_names = [first.faker_for("contacts").name() for _ in range(3)]
    second_names = [second.faker_for("contacts").name() for _ in range(3)]

    assert first_names == second_names


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
