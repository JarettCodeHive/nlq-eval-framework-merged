"""Sales generation configuration helpers."""

from __future__ import annotations

from typing import Any

from generators.core.base import DEFAULT_CONFIG_ROOT
from generators.core.base import GenerationSettings
from generators.core.config import load_domain_config
from generators.core.config import load_json_object


SALES_CONFIG_PATH = DEFAULT_CONFIG_ROOT / "sales.json"
BASE_CONFIG_PATH = DEFAULT_CONFIG_ROOT / "base.json"


def load_base_config() -> dict[str, Any]:
    """Load shared generation configuration."""

    return load_json_object(BASE_CONFIG_PATH)


def load_sales_config() -> dict[str, Any]:
    """Load and assemble the Sales component configuration."""

    return load_domain_config(SALES_CONFIG_PATH, config_root=DEFAULT_CONFIG_ROOT)


def settings_for_profile(profile: str) -> GenerationSettings:
    """Return validated shared generation settings for a Sales profile."""

    return GenerationSettings.from_config_files("sales", profile)


__all__ = [
    "BASE_CONFIG_PATH",
    "SALES_CONFIG_PATH",
    "load_base_config",
    "load_sales_config",
    "settings_for_profile",
]
