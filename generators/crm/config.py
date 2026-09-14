"""CRM generation configuration helpers."""

from __future__ import annotations

from typing import Any

from generators.core.base import DEFAULT_CONFIG_ROOT
from generators.core.base import GenerationSettings
from generators.core.config import load_domain_config
from generators.core.config import load_json_object


CRM_CONFIG_PATH = DEFAULT_CONFIG_ROOT / "crm.json"
BASE_CONFIG_PATH = DEFAULT_CONFIG_ROOT / "base.json"


def load_base_config() -> dict[str, Any]:
    """Load shared generation config."""

    return load_json_object(BASE_CONFIG_PATH)


def load_crm_config() -> dict[str, Any]:
    """Load the monolithic or assembled CRM generation config."""

    return load_domain_config(CRM_CONFIG_PATH, config_root=DEFAULT_CONFIG_ROOT)


def settings_for_profile(profile: str) -> GenerationSettings:
    """Return validated CRM generation settings for a profile."""

    return GenerationSettings.from_config_files("crm", profile)


__all__ = [
    "BASE_CONFIG_PATH",
    "CRM_CONFIG_PATH",
    "load_base_config",
    "load_crm_config",
    "settings_for_profile",
]
