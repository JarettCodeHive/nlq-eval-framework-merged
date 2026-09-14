"""Shared contracts for component-based domain configuration."""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any


DOMAIN_CONFIG_SCHEMA_VERSION = "1.0"
_DESCRIPTOR_KEYS = {"config_schema_version", "domain", "components"}


def load_domain_config(
    config_path: Path,
    config_root: Path | None = None,
) -> dict[str, Any]:
    """Load a monolithic domain config or compose a component descriptor.

    Monolithic JSON objects are returned unchanged. Descriptor entry points are
    validated before their component objects are loaded and merged in descriptor
    order. A top-level section may be owned by only one component.
    """

    entry_point = load_json_object(config_path)
    if not is_domain_config_descriptor(entry_point):
        return entry_point

    component_paths = validate_domain_config_descriptor(
        entry_point,
        config_path,
        config_root=config_root,
    )
    assembled: dict[str, Any] = {}
    section_owners: dict[str, str] = {}
    for component_name, component_path in component_paths.items():
        component = load_json_object(component_path)
        duplicate_sections = sorted(set(assembled).intersection(component))
        if duplicate_sections:
            ownership = {
                section: section_owners[section] for section in duplicate_sections
            }
            raise ValueError(
                f"Domain config component {component_name} duplicates top-level "
                f"sections {duplicate_sections}; existing owners={ownership}"
            )
        assembled.update(component)
        section_owners.update(
            {section_name: component_name for section_name in component}
        )
    return assembled


def load_json_object(path: Path) -> dict[str, Any]:
    """Load a UTF-8 JSON file and require a top-level object."""

    if not path.exists():
        raise FileNotFoundError(f"Missing config file: {path}")
    if not path.is_file():
        raise ValueError(f"Config path is not a file: {path}")
    try:
        with path.open(encoding="utf-8") as config_file:
            raw = json.load(config_file)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Config file contains invalid JSON at line {exc.lineno}, "
            f"column {exc.colno}: {path}"
        ) from exc
    if not isinstance(raw, dict):
        raise ValueError(f"Config file must contain a JSON object: {path}")
    return raw


def is_domain_config_descriptor(config: Mapping[str, Any]) -> bool:
    """Return whether an entry-point object declares component configuration."""

    return "config_schema_version" in config or "components" in config


def validate_domain_config_descriptor(
    descriptor: Mapping[str, Any],
    descriptor_path: Path,
    config_root: Path | None = None,
) -> dict[str, Path]:
    """Validate a domain descriptor and return component paths.

    This keeps only the structural safety checks needed before loading files:
    descriptor shape, version/domain sanity, relative paths under the config
    root, and component file existence. CRM-specific schema/content validation
    is handled after the config is assembled.
    """

    if not isinstance(descriptor, Mapping):
        raise ValueError("Domain config descriptor must be a JSON object")

    actual_keys = set(descriptor)
    if actual_keys != _DESCRIPTOR_KEYS:
        missing = sorted(_DESCRIPTOR_KEYS - actual_keys)
        unexpected = sorted(actual_keys - _DESCRIPTOR_KEYS)
        raise ValueError(
            "Domain config descriptor keys are invalid: "
            f"missing={missing}, unexpected={unexpected}"
        )

    version = descriptor["config_schema_version"]
    if version != DOMAIN_CONFIG_SCHEMA_VERSION:
        raise ValueError(
            "Unsupported domain config descriptor version: "
            f"expected={DOMAIN_CONFIG_SCHEMA_VERSION}, actual={version}"
        )

    expected_domain = descriptor_path.stem
    domain = descriptor["domain"]
    if not isinstance(domain, str) or not domain:
        raise ValueError("Domain config descriptor domain must be a non-empty string")
    if domain != expected_domain:
        raise ValueError(
            "Domain config descriptor domain differs from its filename: "
            f"expected={expected_domain}, actual={domain}"
        )

    components = descriptor["components"]
    if not isinstance(components, Mapping):
        raise ValueError("Domain config descriptor components must be a JSON object")
    if not components:
        raise ValueError("Domain config descriptor components cannot be empty")

    root = (config_root or descriptor_path.parent).resolve()
    resolved: dict[str, Path] = {}
    for component_name, configured_path in components.items():
        if not isinstance(component_name, str) or not component_name:
            raise ValueError("Domain config component names must be non-empty strings")
        if not isinstance(configured_path, str) or not configured_path:
            raise ValueError(
                f"Domain config component {component_name} must be a relative path"
            )

        relative_path = Path(configured_path)
        if relative_path.is_absolute():
            raise ValueError(
                f"Domain config component {component_name} must use a relative path"
            )
        component_path = (root / relative_path).resolve()
        if not component_path.is_relative_to(root):
            raise ValueError(
                f"Domain config component {component_name} escapes config root"
            )
        if not component_path.is_file():
            raise FileNotFoundError(
                f"Missing domain config component {component_name}: {component_path}"
            )
        resolved[component_name] = component_path

    return resolved
