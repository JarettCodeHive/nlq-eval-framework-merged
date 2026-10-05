"""Resolve profile-specific output paths for generated Q&A artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from .release_bundle import active_release_version, component_dir


def load_qa_config(qa_root: Path, generator_dir: str) -> dict:
    """Load the declarative Q&A generator configuration for one domain.

    ``generator_dir`` names the domain's subfolder under ``generator/``
    (e.g. ``"crm"``, ``"sales"``) — required, not defaulted, so this stays
    domain-agnostic rather than silently assuming one domain.
    """

    config_path = qa_root / "generator" / generator_dir / "config.json"
    return json.loads(config_path.read_text(encoding="utf-8"))


def qa_version(qa_root: Path, generator_dir: str) -> str:
    """Compatibility alias for the domain's unified release version."""

    config = load_qa_config(qa_root, generator_dir)
    release_config = json.loads(
        (qa_root.parent / config["dataset"]["release_config_path"]).read_text(encoding="utf-8")
    )
    return active_release_version(release_config)


def resolve_qa_output_dir(qa_root: Path, profile: str, generator_dir: str) -> Path:
    """Resolve the final Q&A package directory for ``dev`` or ``full``.

    Dev artifacts remain disposable under ``tmp``. Full artifacts are written
    to an independently versioned Q&A release directory, because question
    templates can change without requiring a new dataset version.
    """

    config = load_qa_config(qa_root, generator_dir)
    release = config["qa_release"]
    outputs = release["profile_outputs"]
    if profile not in outputs:
        raise ValueError(f"Unsupported Q&A output profile: {profile}")

    version = qa_version(qa_root, generator_dir)
    relative_path = str(outputs[profile]).format(
        domain=config["domain"],
        qa_version=version,
        release_version=version,
        profile=profile,
    )
    if profile == "full":
        return component_dir(
            config["domain"], "qa_pairs", version, repo_root=qa_root.parent
        )
    return qa_root.parent / relative_path
