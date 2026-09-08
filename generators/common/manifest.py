"""Manifest generation and artifact hashing helpers."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FileHash:
    """SHA-256 metadata for one artifact."""

    path: Path
    sha256: str
    bytes: int


def build_distribution_metadata(
    domain_distributions: Mapping[str, Any],
    effective_distributions: Mapping[str, Any],
    distribution_targets: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Describe configured presets, overrides, effective values, and targets."""

    metadata: dict[str, dict[str, Any]] = {}
    for settings_key, domain_spec in domain_distributions.items():
        matching_targets = {
            target_name: deepcopy(dict(target))
            for target_name, target in distribution_targets.items()
            if target.get("settings_key") == settings_key
        }
        metadata[str(settings_key)] = {
            "preset": domain_spec["preset"],
            "overrides": deepcopy(
                {key: value for key, value in domain_spec.items() if key != "preset"}
            ),
            "effective_parameters": deepcopy(
                dict(effective_distributions[settings_key])
            ),
            "targets": matching_targets,
        }
    return metadata


def compute_sha256(path: Path, chunk_size: int = 1024 * 1024) -> FileHash:
    """Compute SHA-256 for a file without loading it all into memory."""

    if not path.exists():
        raise FileNotFoundError(f"Missing file for SHA-256 hashing: {path}")
    if not path.is_file():
        raise ValueError(f"SHA-256 target is not a file: {path}")

    digest = sha256()
    byte_count = 0
    with path.open("rb") as artifact:
        while True:
            chunk = artifact.read(chunk_size)
            if not chunk:
                break
            byte_count += len(chunk)
            digest.update(chunk)

    return FileHash(path=path, sha256=digest.hexdigest(), bytes=byte_count)


def compute_file_hashes(paths: list[Path] | tuple[Path, ...]) -> list[FileHash]:
    """Compute SHA-256 hashes for paths in the supplied order."""

    return [compute_sha256(path) for path in paths]


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """Write a deterministic manifest JSON file atomically."""

    if path.exists():
        raise FileExistsError(
            f"Refusing to overwrite immutable release manifest: {path}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8", newline="\n") as manifest_file:
        json.dump(manifest, manifest_file, indent=2, sort_keys=True)
        manifest_file.write("\n")
    tmp_path.replace(path)
