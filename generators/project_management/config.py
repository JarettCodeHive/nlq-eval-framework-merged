"""Project Management generation configuration helpers."""

from __future__ import annotations

from typing import Any

from generators.core.base import DEFAULT_CONFIG_ROOT
from generators.core.base import GenerationSettings
from generators.core.config import load_domain_config
from generators.core.config import load_json_object


PROJECT_MANAGEMENT_CONFIG_PATH = DEFAULT_CONFIG_ROOT / "project_management.json"
BASE_CONFIG_PATH = DEFAULT_CONFIG_ROOT / "base.json"


def load_base_config() -> dict[str, Any]:
    """Load shared generation configuration."""

    return load_json_object(BASE_CONFIG_PATH)


def load_project_management_config() -> dict[str, Any]:
    """Load and assemble the Project Management component configuration."""

    return load_domain_config(
        PROJECT_MANAGEMENT_CONFIG_PATH,
        config_root=DEFAULT_CONFIG_ROOT,
    )


def settings_for_profile(profile: str) -> GenerationSettings:
    """Return shared generation settings for a Project Management profile."""

    return GenerationSettings.from_config_files("project_management", profile)


__all__ = [
    "BASE_CONFIG_PATH",
    "PROJECT_MANAGEMENT_CONFIG_PATH",
    "load_base_config",
    "load_project_management_config",
    "settings_for_profile",
]
