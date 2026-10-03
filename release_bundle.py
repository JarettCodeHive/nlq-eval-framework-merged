"""Shared release-bundle identity and path resolution.

A release version identifies the complete evaluation asset set for one domain:
dataset, Q&A pairs, judge evidence, and scorecards.  The root CLI selects the
version once and every component resolves its paths through this module.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import json
import re
from pathlib import Path
from typing import Iterator


REPO_ROOT = Path(__file__).resolve().parent
_VERSION = ContextVar[str | None]("nlq_release_version", default=None)
_VALID_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def validate_release_version(version: str) -> str:
    """Return a safe release-directory name or raise a useful error."""

    value = version.strip()
    if not value or not _VALID_VERSION.fullmatch(value) or value in {".", ".."}:
        raise ValueError(
            "release version must be one directory-safe value containing only "
            "letters, numbers, '.', '_' or '-'"
        )
    return value


def selected_release_version() -> str | None:
    """Return the release version selected by the current CLI invocation."""

    return _VERSION.get()


@contextmanager
def use_release_version(version: str | None) -> Iterator[None]:
    """Make one release version visible to every pipeline component."""

    token = _VERSION.set(validate_release_version(version) if version else None)
    try:
        yield
    finally:
        _VERSION.reset(token)


def configured_release_version(config: dict) -> str:
    """Resolve the canonical version, accepting old configs during migration."""

    configured = config.get("release_version") or config.get("dataset_version")
    if not configured:
        raise ValueError("release configuration is missing release_version")
    value = str(configured)
    # Old configuration used dataset-vX.Y.Z.  New bundles use vX.Y.Z.
    if "release_version" not in config and value.startswith("dataset-"):
        value = value.removeprefix("dataset-")
    return validate_release_version(value)


def active_release_version(config: dict) -> str:
    """Return the CLI-selected version, or the configured default."""

    return selected_release_version() or configured_release_version(config)


def bundle_root(domain: str, version: str | None = None) -> Path:
    """Return ``release/<domain>/<version>`` for the active release."""

    selected = version or selected_release_version() or default_release_version(domain)
    return REPO_ROOT / "release" / domain / validate_release_version(selected)


def default_release_version(domain: str) -> str:
    """Read a domain's checked-in default release version."""

    path = REPO_ROOT / "config" / "generation" / domain / "release.json"
    if not path.is_file():
        raise ValueError(f"no release configuration exists for domain {domain!r}")
    config = json.loads(path.read_text(encoding="utf-8"))
    return configured_release_version(config)


def component_dir(domain: str, component: str, version: str | None = None) -> Path:
    """Return one component directory inside a versioned release bundle."""

    if component not in {"dataset", "qa_pairs", "judge", "scorecard"}:
        raise ValueError(f"unknown release component: {component}")
    return bundle_root(domain, version) / component
