"""Logistics generation configuration helpers."""

from __future__ import annotations

from typing import Any

from generators.core.base import DEFAULT_CONFIG_ROOT
from generators.core.base import GenerationSettings
from generators.core.config import load_domain_config
from generators.core.config import load_json_object


LOGISTICS_CONFIG_PATH = DEFAULT_CONFIG_ROOT / "logistics.json"
BASE_CONFIG_PATH = DEFAULT_CONFIG_ROOT / "base.json"


def load_base_config() -> dict[str, Any]:
    """Load shared generation configuration."""

    return load_json_object(BASE_CONFIG_PATH)


def load_logistics_config() -> dict[str, Any]:
    """Load and assemble the Logistics component configuration."""

    return load_domain_config(
        LOGISTICS_CONFIG_PATH,
        config_root=DEFAULT_CONFIG_ROOT,
    )


def settings_for_profile(profile: str) -> GenerationSettings:
    """Return shared generation settings for a Logistics profile."""

    return GenerationSettings.from_config_files("logistics", profile)


__all__ = [
    "BASE_CONFIG_PATH",
    "LOGISTICS_CONFIG_PATH",
    "load_base_config",
    "load_logistics_config",
    "settings_for_profile",
]
