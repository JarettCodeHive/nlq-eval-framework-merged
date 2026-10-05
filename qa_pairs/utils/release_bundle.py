"""Shared release-bundle identity and path resolution."""

from __future__ import annotations

import json
import re
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_VERSION = ContextVar[str | None]("nlq_release_version", default=None)
_VALID_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_VALID_COMPONENTS = frozenset(
    {"dataset", "qa_pairs", "judge", "scorecard", "platform", "logs"}
)


class LegacyReleaseLayoutWarning(UserWarning):
    """A read resolved from the retired domain-first release layout."""


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
    if "release_version" not in config and value.startswith("dataset-"):
        value = value.removeprefix("dataset-")
    return validate_release_version(value)


def active_release_version(config: dict) -> str:
    """Return the CLI-selected version, or the configured default."""

    return selected_release_version() or configured_release_version(config)


def default_release_version(
    domain: str, *, repo_root: Path | None = None
) -> str:
    """Read a domain's checked-in default release version."""

    path = (
        (repo_root or REPO_ROOT)
        / "config"
        / "generation"
        / domain
        / "release.json"
    )
    if not path.is_file():
        raise ValueError(f"no release configuration exists for domain {domain!r}")
    config = json.loads(path.read_text(encoding="utf-8"))
    return configured_release_version(config)


def release_root(
    version: str | None = None,
    *,
    default_domain: str = "crm",
    repo_root: Path | None = None,
) -> Path:
    """Return the canonical ``release/<version>`` root."""

    selected = (
        version
        or selected_release_version()
        or default_release_version(default_domain, repo_root=repo_root)
    )
    return (repo_root or REPO_ROOT) / "release" / validate_release_version(selected)


def bundle_root(
    domain: str,
    version: str | None = None,
    *,
    repo_root: Path | None = None,
) -> Path:
    """Return canonical ``release/<version>/<domain>`` for one domain."""

    return release_root(
        version, default_domain=domain, repo_root=repo_root
    ) / domain


def legacy_bundle_root(
    domain: str,
    version: str | None = None,
    *,
    repo_root: Path | None = None,
) -> Path:
    """Return the retired ``release/<domain>/<version>`` location."""

    selected = (
        version
        or selected_release_version()
        or default_release_version(domain, repo_root=repo_root)
    )
    return (
        (repo_root or REPO_ROOT)
        / "release"
        / domain
        / validate_release_version(selected)
    )


def component_dir(
    domain: str,
    component: str,
    version: str | None = None,
    *,
    repo_root: Path | None = None,
) -> Path:
    """Return a canonical write location inside a release bundle."""

    if component not in _VALID_COMPONENTS:
        raise ValueError(f"unknown release component: {component}")
    return bundle_root(domain, version, repo_root=repo_root) / component


def legacy_component_dir(
    domain: str,
    component: str,
    version: str | None = None,
    *,
    repo_root: Path | None = None,
) -> Path:
    """Return one component in the retired domain-first layout."""

    if component not in _VALID_COMPONENTS:
        raise ValueError(f"unknown release component: {component}")
    return legacy_bundle_root(
        domain, version, repo_root=repo_root
    ) / component


def _warn_legacy(legacy: Path, canonical: Path) -> None:
    warnings.warn(
        f"reading legacy release layout at {legacy}; new artifacts are "
        f"written to {canonical}",
        LegacyReleaseLayoutWarning,
        stacklevel=3,
    )


def existing_component_dir(
    domain: str,
    component: str,
    version: str | None = None,
    *,
    repo_root: Path | None = None,
) -> Path:
    """Resolve a read from canonical layout, then legacy layout if necessary.

    New writes must use :func:`component_dir`. This fallback is deliberately
    read-only so the repository never produces two layouts for one release.
    """

    canonical = component_dir(
        domain, component, version, repo_root=repo_root
    )
    if canonical.exists():
        return canonical
    legacy = legacy_component_dir(
        domain, component, version, repo_root=repo_root
    )
    if legacy.exists():
        _warn_legacy(legacy, canonical)
        return legacy
    return canonical


def existing_component_path(
    domain: str,
    component: str,
    *parts: str,
    version: str | None = None,
    repo_root: Path | None = None,
) -> Path:
    """Resolve a specific child path with legacy fallback."""

    canonical = component_dir(
        domain, component, version, repo_root=repo_root
    ).joinpath(*parts)
    if canonical.exists():
        return canonical
    legacy = legacy_component_dir(
        domain, component, version, repo_root=repo_root
    ).joinpath(*parts)
    if legacy.exists():
        _warn_legacy(legacy, canonical)
        return legacy
    return canonical
