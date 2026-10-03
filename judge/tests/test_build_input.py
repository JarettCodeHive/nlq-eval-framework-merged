"""Regression tests for joining base Q&A rows and rephrase variants."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from judge.build_input import build


CONTRACT_FIELDS = [
    "question_id",
    "tier",
    "natural_language_question",
    "expected_answer",
    "reference_sql",
    "reference_tables",
    "reference_fields",
    "judge_reference",
    "derivation_rationale",
    "rephrase_group_id",
    "is_release_160",
]
COMPANION_FIELDS = [
    "question_id",
    "tier",
    "family",
    "scoring_mode",
    "rephrase_group_id",
    "natural_language_question",
]


def _write(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _contract_row(**overrides: str) -> dict[str, str]:
    row = {
        "question_id": "CRM-T1-01-01",
        "tier": "T1",
        "natural_language_question": "How many accounts?",
        "expected_answer": "72",
        "reference_sql": "SELECT 72;",
        "reference_tables": "accounts",
        "reference_fields": "accounts.account_id",
        "judge_reference": "There are 72 accounts.",
        "derivation_rationale": "Count the accounts.",
        "rephrase_group_id": "CRM-RG-01",
        "is_release_160": "true",
    }
    row.update(overrides)
    return row


def _companion_row(**overrides: str) -> dict[str, str]:
    row = {
        "question_id": "CRM-T1-01-01",
        "tier": "T1",
        "family": "T1-01",
        "scoring_mode": "scalar_exact",
        "rephrase_group_id": "CRM-RG-01",
        "natural_language_question": "How many accounts?",
    }
    row.update(overrides)
    return row


def _package(
    tmp_path: Path,
    contract: list[dict[str, str]],
    companion: list[dict[str, str]],
) -> tuple[Path, Path]:
    _write(tmp_path / "crm_qa_pairs.csv", CONTRACT_FIELDS, contract)
    _write(tmp_path / "crm_qa_pairs_companion.csv", COMPANION_FIELDS, companion)
    return tmp_path, tmp_path / "crm_judge_input.csv"


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_build_includes_contract_only_rephrase_variants(tmp_path: Path) -> None:
    base = _contract_row()
    variant = _contract_row(
        question_id="CRM-RG-01-V01",
        natural_language_question="What is the account count?",
        is_release_160="false",
    )
    package, output = _package(tmp_path, [base, variant], [_companion_row()])

    assert build(package, "crm", output) == 2

    rows = _read(output)
    assert [row["question_id"] for row in rows] == [
        "CRM-T1-01-01",
        "CRM-RG-01-V01",
    ]
    assert rows[1]["tier"] == "T1"
    assert rows[1]["family"] == "T1-01"
    assert rows[1]["scoring_mode"] == "scalar_exact"
    assert rows[1]["rephrase_group_id"] == "CRM-RG-01"


def test_build_rejects_contract_only_row_without_a_group(tmp_path: Path) -> None:
    variant = _contract_row(
        question_id="CRM-RG-01-V01",
        natural_language_question="What is the account count?",
        rephrase_group_id="",
        is_release_160="false",
    )
    package, output = _package(
        tmp_path,
        [_contract_row(), variant],
        [_companion_row(rephrase_group_id="")],
    )

    with pytest.raises(SystemExit, match="no companion match and no rephrase_group_id"):
        build(package, "crm", output)


def test_build_rejects_variant_group_without_exactly_one_base(
    tmp_path: Path,
) -> None:
    variant = _contract_row(
        question_id="CRM-RG-02-V01",
        natural_language_question="What is the account count?",
        rephrase_group_id="CRM-RG-02",
        is_release_160="false",
    )
    package, output = _package(
        tmp_path,
        [_contract_row(), variant],
        [_companion_row()],
    )

    with pytest.raises(SystemExit, match="must have exactly one companion base"):
        build(package, "crm", output)
