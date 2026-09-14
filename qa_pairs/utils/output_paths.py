"""Resolve profile-specific output paths for generated Q&A artifacts."""

from __future__ import annotations

import json
from pathlib import Path


def load_qa_config(qa_root: Path) -> dict:
    """Load the declarative Q&A generator configuration."""

    config_path = qa_root / "generator" / "config.json"
    return json.loads(config_path.read_text(encoding="utf-8"))


def qa_version(qa_root: Path) -> str:
    """Return the configured Q&A release version."""

    return str(load_qa_config(qa_root)["qa_release"]["version"])


def resolve_qa_output_dir(qa_root: Path, profile: str) -> Path:
    """Resolve the final Q&A package directory for ``dev`` or ``full``.

    Dev artifacts remain disposable under ``tmp``. Full artifacts are written
    to an independently versioned Q&A release directory, because question
    templates can change without requiring a new dataset version.
    """

    config = load_qa_config(qa_root)
    release = config["qa_release"]
    outputs = release["profile_outputs"]
    if profile not in outputs:
        raise ValueError(f"Unsupported Q&A output profile: {profile}")

    relative_path = str(outputs[profile]).format(
        domain=config["domain"],
        qa_version=release["version"],
        profile=profile,
    )
    return qa_root.parent / relative_path
