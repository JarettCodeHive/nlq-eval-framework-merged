"""Rephrase groups: every variant in a group must return identical
numerics (scope doc Section 9.5)."""

import csv
from pathlib import Path

import pytest

from utils.output_paths import resolve_qa_output_dir

QA_ROOT = Path(__file__).resolve().parent.parent
MAP = resolve_qa_output_dir(QA_ROOT, "full", "crm") / "rephrase" / "crm_rephrase_map.csv"
PAIRS = MAP.with_name("crm_rephrase_pairs.csv")

pytestmark = pytest.mark.skipif(
    not MAP.exists(), reason="run generator/rephrase.py --profile full first"
)


def _rows(p):
    with p.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_every_group_shares_one_result_hash():
    groups = {}
    for r in _rows(MAP):
        groups.setdefault(r["pair_group_id"], set()).add(r["result_hash"])
    for gid, hashes in groups.items():
        assert len(hashes) == 1, f"{gid} variants disagree: {hashes}"


def test_every_group_has_at_least_two_variants():
    counts = {}
    for r in _rows(MAP):
        counts[r["pair_group_id"]] = counts.get(r["pair_group_id"], 0) + 1
    assert all(n >= 2 for n in counts.values()), counts


def test_exactly_one_base_per_group():
    bases = {}
    for r in _rows(MAP):
        if r["is_base"] == "yes":
            bases[r["pair_group_id"]] = bases.get(r["pair_group_id"], 0) + 1
    assert all(n == 1 for n in bases.values()), bases


def test_rephrase_pairs_are_seven_field_contract():
    assert list(_rows(PAIRS)[0].keys()) == [
        "natural_language_question",
        "expected_answer",
        "reference_sql",
        "reference_tables",
        "reference_fields",
        "judge_reference",
        "derivation_rationale",
    ]


def test_variants_in_a_group_share_reference_sql():
    sql_by_question = {r["natural_language_question"]: r["reference_sql"] for r in _rows(PAIRS)}
    by_group = {}
    for m in _rows(MAP):
        if m["is_base"] == "no":
            by_group.setdefault(m["pair_group_id"], set()).add(
                sql_by_question[m["natural_language_question"]]
            )
    for gid, sqls in by_group.items():
        assert len(sqls) == 1, f"{gid} variants have different SQL"


def test_bases_are_real_160_release_pairs():
    """Every group's base question_id is a CRM-T*-** in the release, not RG-*."""
    for m in _rows(MAP):
        if m["is_base"] == "yes":
            assert m["question_id"].startswith("CRM-T"), m["question_id"]


def test_roughly_ten_percent_of_the_release_are_group_bases():
    bases = [m for m in _rows(MAP) if m["is_base"] == "yes"]
    assert 12 <= len(bases) <= 20, f"{len(bases)} bases (~10% of 160 expected)"
