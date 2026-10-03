"""Unified evaluation-release path contract."""

from pathlib import Path

import pytest

from generators.core.base import GenerationSettings
from judge.cli import run_output_dir
from judge.config import load_judge_config
from qa_pairs.utils.dataset_source import resolve_dataset_source
from qa_pairs.utils.output_paths import resolve_qa_output_dir
from release_bundle import REPO_ROOT, use_release_version, validate_release_version
from scorecard.config import report_output_dir


QA_ROOT = REPO_ROOT / "qa_pairs"


def test_one_cli_version_resolves_every_full_release_component() -> None:
    with use_release_version("client-2.4.0"):
        dataset = GenerationSettings.from_config_files("crm", "full")
        qa_source = resolve_dataset_source(QA_ROOT, "full", "crm")
        qa_output = resolve_qa_output_dir(QA_ROOT, "full", "crm")
        judge_output = run_output_dir(
            "crm", "run-123", load_judge_config("crm")
        )
        scorecard_output = report_output_dir("crm", "run-123")

    root = REPO_ROOT / "release" / "crm" / "client-2.4.0"
    assert dataset.release_version == "client-2.4.0"
    assert dataset.output_path == root / "dataset"
    assert qa_source.csv_dir == root / "dataset"
    assert qa_source.dataset_version == dataset.release_version
    assert qa_output == root / "qa_pairs"
    assert judge_output == root / "judge" / "run-123"
    assert scorecard_output == root / "scorecard" / "run-123"


@pytest.mark.parametrize("version", ["", "../escape", "a/b", "a b"])
def test_release_version_must_be_one_safe_directory_name(version: str) -> None:
    with pytest.raises(ValueError, match="release version"):
        validate_release_version(version)

