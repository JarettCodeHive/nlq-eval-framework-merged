from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils.dataset_source import resolve_dataset_source

QA_ROOT = Path(__file__).resolve().parent.parent


def test_dev_source_is_generator_imperfect_preview() -> None:
    source = resolve_dataset_source(QA_ROOT, "dev")

    assert source.source_label == "tmp/generated/crm/dev/imperfect"
    assert source.dataset_version == "dataset-v1.0.0"


def test_full_source_uses_configured_dataset_version() -> None:
    source = resolve_dataset_source(QA_ROOT, "full")
    release_config = json.loads(source.release_config_path.read_text(encoding="utf-8"))
    expected_version = release_config["dataset_version"]

    assert source.source_label == f"release/crm/{expected_version}"
    assert source.ddl_path.name == "crm_ddl.sql"
    assert source.dbml_path.name == "crm_er.dbml"


def test_unknown_source_profile_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported dataset profile"):
        resolve_dataset_source(QA_ROOT, "preview")
