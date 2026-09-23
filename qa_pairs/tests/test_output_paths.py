from __future__ import annotations

from pathlib import Path

import pytest

from utils.output_paths import qa_version, resolve_qa_output_dir

QA_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = QA_ROOT.parent


def test_dev_qa_output_is_disposable() -> None:
    output = resolve_qa_output_dir(QA_ROOT, "dev", "crm")

    assert output == REPO_ROOT / "tmp/generated/crm/dev/qa_pairs"


def test_full_qa_output_is_independently_versioned() -> None:
    output = resolve_qa_output_dir(QA_ROOT, "full", "crm")

    assert output == REPO_ROOT / f"release/crm/qa-pairs-v{qa_version(QA_ROOT, 'crm')}"


def test_unknown_qa_output_profile_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported Q&A output profile"):
        resolve_qa_output_dir(QA_ROOT, "preview", "crm")
