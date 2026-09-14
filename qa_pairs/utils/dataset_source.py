"""Resolve Q&A dataset inputs from the repository-level generator outputs."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class DatasetSource:
    """Resolved source and contract files for one Q&A generation profile."""

    profile: str
    dataset_version: str
    csv_dir: Path
    base_config_path: Path
    release_config_path: Path
    ddl_path: Path
    dbml_path: Path
    repo_root: Path

    @property
    def source_label(self) -> str:
        """Return a stable repository-relative source path for manifests."""

        return self.csv_dir.relative_to(self.repo_root).as_posix()


def resolve_dataset_source(qa_root: Path, profile: str) -> DatasetSource:
    """Resolve a profile to generator-owned CSV and schema paths.

    Paths in ``generator/config.json`` are relative to the repository root.
    The dataset version comes from the canonical CRM release config and is
    interpolated into the full-profile path without a Python code change.
    """

    config_path = qa_root / "generator" / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    dataset = config["dataset"]
    sources = dataset["profile_sources"]
    if profile not in sources:
        raise ValueError(f"Unsupported dataset profile: {profile}")

    repo_root = qa_root.parent
    release_config_path = repo_root / dataset["release_config_path"]
    release_config = json.loads(release_config_path.read_text(encoding="utf-8"))
    dataset_version = str(release_config["dataset_version"])
    source_path = str(sources[profile]).format(
        dataset_version=dataset_version,
    )
    resolved = DatasetSource(
        profile=profile,
        dataset_version=dataset_version,
        csv_dir=repo_root / source_path,
        base_config_path=repo_root / dataset["base_config_path"],
        release_config_path=release_config_path,
        ddl_path=repo_root / dataset["schema_ddl_path"],
        dbml_path=repo_root / dataset["schema_dbml_path"],
        repo_root=repo_root,
    )
    return resolved
