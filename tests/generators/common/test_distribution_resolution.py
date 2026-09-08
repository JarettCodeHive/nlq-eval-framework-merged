from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from generators.common.base import GenerationSettings
from generators.common.base import resolve_distribution_settings
from generators.crm.config import load_base_config
from generators.crm.config import load_crm_config


def _future_config_pair() -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the planned config shape without modifying repository config files."""

    base_config = deepcopy(load_base_config())
    crm_config = deepcopy(load_crm_config())
    return base_config, crm_config


def test_shared_config_defines_only_domain_neutral_distribution_presets() -> None:
    base_config, _ = _future_config_pair()

    assert set(base_config["distribution_defaults"]) == {
        "pareto_amount",
        "poisson_frequency",
        "gaussian_mixture_dates",
    }
    assert "distributions" not in base_config
    assert all(
        "campaign" not in preset_name
        for preset_name in base_config["distribution_defaults"]
    )


def test_distribution_targets_reference_domain_settings() -> None:
    base_config, crm_config = _future_config_pair()
    statistical_targets = {
        name: target
        for name, target in crm_config["distribution_targets"].items()
        if "distribution" in target
    }

    assert statistical_targets
    for target_name, target in statistical_targets.items():
        assert "parameter_source" not in target
        settings_key = target["settings_key"]
        assert settings_key in crm_config["distributions"]
        preset_name = crm_config["distributions"][settings_key]["preset"]
        preset = base_config["distribution_defaults"][preset_name]
        assert target["distribution"] == preset["name"], target_name


def test_domain_distribution_inherits_shared_preset() -> None:
    base_config, crm_config = _future_config_pair()

    settings = GenerationSettings.from_configs(base_config, crm_config, "dev")

    assert settings.distributions["campaign_budget"] == {
        "name": "pareto",
        "alpha": 1.16,
        "min_amount": 5000,
        "max_amount": 2_000_000,
        "scale": 2,
    }


def test_domain_distribution_overrides_selected_preset_values() -> None:
    base_config, crm_config = _future_config_pair()
    crm_config["distributions"]["campaign_budget"].update(
        {
            "min_amount": 10_000,
            "max_amount": 1_000_000,
        }
    )

    settings = GenerationSettings.from_configs(base_config, crm_config, "dev")

    assert settings.distributions["campaign_budget"] == {
        "name": "pareto",
        "alpha": 1.16,
        "min_amount": 10_000,
        "max_amount": 1_000_000,
        "scale": 2,
    }


def test_distribution_resolution_does_not_mutate_source_configs() -> None:
    base_config, crm_config = _future_config_pair()
    original_base = deepcopy(base_config)
    original_crm = deepcopy(crm_config)

    GenerationSettings.from_configs(base_config, crm_config, "dev")

    assert base_config == original_base
    assert crm_config == original_crm


def test_unknown_distribution_preset_is_rejected() -> None:
    base_config, crm_config = _future_config_pair()
    crm_config["distributions"]["campaign_budget"]["preset"] = "missing"

    with pytest.raises(ValueError, match="unknown distribution preset.*missing"):
        GenerationSettings.from_configs(base_config, crm_config, "dev")


def test_distribution_without_preset_is_rejected() -> None:
    base_config, crm_config = _future_config_pair()
    del crm_config["distributions"]["campaign_budget"]["preset"]

    with pytest.raises(ValueError, match="campaign_budget must define a preset"):
        GenerationSettings.from_configs(base_config, crm_config, "dev")


def test_non_object_distribution_preset_is_rejected() -> None:
    base_config, crm_config = _future_config_pair()
    base_config["distribution_defaults"]["pareto_amount"] = []

    with pytest.raises(ValueError, match="pareto_amount must be an object"):
        GenerationSettings.from_configs(base_config, crm_config, "dev")


def test_non_object_domain_distribution_is_rejected() -> None:
    base_config, crm_config = _future_config_pair()
    crm_config["distributions"]["campaign_budget"] = []

    with pytest.raises(ValueError, match="campaign_budget must be an object"):
        GenerationSettings.from_configs(base_config, crm_config, "dev")


def test_algorithm_incompatible_override_is_rejected() -> None:
    base_config, crm_config = _future_config_pair()
    crm_config["distributions"]["campaign_budget"]["lambda"] = 4.0

    with pytest.raises(ValueError, match="unsupported.*lambda.*pareto"):
        GenerationSettings.from_configs(base_config, crm_config, "dev")


def test_invalid_effective_domain_override_is_rejected() -> None:
    base_config, crm_config = _future_config_pair()
    crm_config["distributions"]["campaign_budget"]["max_amount"] = 100

    with pytest.raises(
        ValueError,
        match="max_amount must be greater than or equal to min_amount",
    ):
        GenerationSettings.from_configs(base_config, crm_config, "dev")


def test_resolved_crm_distributions_match_pre_migration_values() -> None:
    base_config, crm_config = _future_config_pair()

    settings = GenerationSettings.from_configs(base_config, crm_config, "dev")

    assert settings.distributions == {
        "campaign_budget": {
            "name": "pareto",
            "alpha": 1.16,
            "min_amount": 5000,
            "max_amount": 2_000_000,
            "scale": 2,
        },
        "interaction_frequency": {
            "name": "poisson",
            "lambda": 3.2,
        },
        "date_clustering": {
            "name": "gaussian_mixture",
            "components": 3,
            "component_centers": [0.2, 0.55, 0.85],
            "component_weights": [0.35, 0.35, 0.3],
            "std_fraction": 0.035,
        },
    }


@pytest.mark.parametrize(
    ("preset_name", "parameter", "value", "message"),
    [
        ("pareto_amount", "alpha", 0, "alpha must be positive"),
        ("pareto_amount", "min_amount", 0, "min_amount must be positive"),
        (
            "pareto_amount",
            "max_amount",
            4999,
            "max_amount must be greater than or equal to min_amount",
        ),
        ("pareto_amount", "scale", -1, "scale must be a non-negative integer"),
        ("pareto_amount", "scale", 2.5, "scale must be a non-negative integer"),
        ("poisson_frequency", "lambda", 0, "lambda must be positive"),
        (
            "gaussian_mixture_dates",
            "components",
            2,
            "components must match component_centers length",
        ),
        (
            "gaussian_mixture_dates",
            "component_centers",
            [0.2, 0.55, 1.2],
            "component_centers values must be between 0 and 1",
        ),
        (
            "gaussian_mixture_dates",
            "component_weights",
            [0.35, -0.05, 0.7],
            "component_weights values cannot be negative",
        ),
        (
            "gaussian_mixture_dates",
            "component_weights",
            [0.2, 0.2, 0.2],
            "component_weights must sum to 1.0",
        ),
        (
            "gaussian_mixture_dates",
            "std_fraction",
            0,
            "std_fraction must be positive",
        ),
    ],
)
def test_invalid_distribution_parameters_are_rejected(
    preset_name: str,
    parameter: str,
    value: Any,
    message: str,
) -> None:
    base_config, crm_config = _future_config_pair()
    base_config["distribution_defaults"][preset_name][parameter] = value

    with pytest.raises(ValueError, match=message):
        resolve_distribution_settings(
            base_config["distribution_defaults"],
            crm_config["distributions"],
        )


def test_missing_required_distribution_parameter_is_rejected() -> None:
    base_config, crm_config = _future_config_pair()
    del base_config["distribution_defaults"]["pareto_amount"]["alpha"]

    with pytest.raises(ValueError, match="missing parameters.*alpha"):
        resolve_distribution_settings(
            base_config["distribution_defaults"],
            crm_config["distributions"],
        )


def test_unsupported_preset_parameter_is_rejected() -> None:
    base_config, crm_config = _future_config_pair()
    base_config["distribution_defaults"]["poisson_frequency"]["scale"] = 2

    with pytest.raises(ValueError, match="unsupported parameters.*poisson.*scale"):
        resolve_distribution_settings(
            base_config["distribution_defaults"],
            crm_config["distributions"],
        )
