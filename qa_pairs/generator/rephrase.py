"""
Rephrase groups (scope doc Section 9.5): the same underlying query asked
2+ ways must return identical numerics.

Each group names a BASE pair already in the 160 (by family + parameter).
This script looks that pair up in the delivered contract + companion,
reuses its exact reference_sql / expected_answer / result_hash, and
attaches reworded variants. ~16 of the 160 are group bases (~10%).

Rewrites crm_qa_pairs.csv / crm_qa_pairs_companion.csv in place: fills
rephrase_group_id on the 16 group-base rows and appends the 19 variant
rows (is_release_160=false), so the eval tool sees the whole release -
including rephrase groups - in one file.

Also writes (<resolved Q&A package>/rephrase/), review-only:
  crm_rephrase_pairs.csv   7-field contract - the reworded variant rows only
  crm_rephrase_map.csv     pair_group_id -> question_id, is_base, class, hash
  verification_logs/*.json

Run AFTER scale_pairs.py.

    python generator/rephrase.py --profile dev|full
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from utils import build_stamp, verify  # noqa: E402
from utils.duckdb_io import connect_typed  # noqa: E402
from utils.output_paths import resolve_qa_output_dir  # noqa: E402

ID_PREFIX = json.loads((BASE / "generator" / "config.json").read_text())["id_prefix"]


def _gid(group: str) -> str:
    """Domain-prefixed rephrase-group id, e.g. RG-01 -> CRM-RG-01."""
    return f"{ID_PREFIX}-{group}"


# group -> (family, {param: value}, class, [reworded variants]).
# The base value must be one the family actually sampled (index-0 values
# are always sampled by even_sample).
GROUPS = {
    "RG-01": (
        "T5-04",
        {"region": "Central"},
        "Temporal",
        [
            "For Central-region accounts, compare total engagement points in the "
            "previous quarter with the quarter before it.",
            "How did total engagement points among Central-region accounts change "
            "from the January-March 2026 period to the April-June 2026 period?",
        ],
    ),
    "RG-02": (
        "T5-01",
        {"region": "Central"},
        "Lexical",
        [
            "Among Central-region accounts, which three campaign types generated "
            "the most total engagement points?",
            "For Central-region accounts, name the top three campaign types by "
            "total engagement score.",
        ],
    ),
    "RG-03": (
        "T1-01",
        {"campaign_type": "Awareness"},
        "Lexical",
        [
            "What is the combined planned spend across all awareness campaigns?",
            "How much budget is allocated to awareness campaigns in total?",
        ],
    ),
    "RG-04": (
        "T3-01",
        {"region": "Central"},
        "Rephrase",
        ["Among Central-region accounts, how many are missing any contact record?"],
    ),
    "RG-05": (
        "T5-02",
        {"category": "Access"},
        "Lexical",
        [
            "Right now, which accounts are carrying the biggest unresolved queue "
            "of access support cases?"
        ],
    ),
    "RG-06": (
        "T5-05",
        {"customer_tier": "Preferred"},
        "Temporal",
        [
            "Looking at preferred-tier accounts, which region's total engagement "
            "points held up best between the January-March and April-June 2026 "
            "periods?"
        ],
    ),
    "RG-07": (
        "T2-05",
        {"is_active": "true"},
        "Lexical",
        ["For active contacts, what is the split between inbound and outbound " "interactions?"],
    ),
    "RG-08": (
        "T3-03",
        {"title": "Business Analyst"},
        "Rephrase",
        ["Of the contacts titled Business Analyst, how many have we never " "interacted with?"],
    ),
    "RG-09": (
        "T2-01",
        {"region": "Central"},
        "Rephrase",
        ["How many support cases were opened by accounts located in the Central " "region?"],
    ),
    "RG-10": (
        "T2-03",
        {"industry": "Education"},
        "Lexical",
        ["Across our education-sector accounts, how many contacts do we hold?"],
    ),
    "RG-11": (
        "T1-03",
        {"priority": "Critical"},
        "Lexical",
        ["How many support tickets are marked critical?"],
    ),
    "RG-12": (
        "T2-08",
        {"industry": "Education"},
        "Rephrase",
        ["For education-industry accounts, what share of support cases met the " "SLA deadline?"],
    ),
    "RG-13": (
        "T4-01",
        {"region": "Central"},
        "Rephrase",
        ["For Central-region accounts, give the engagement total for each " "interaction channel."],
    ),
    "RG-14": (
        "T5-03",
        {"industry": "Education"},
        "Rephrase",
        [
            "Among education-industry accounts, which ones have at least 3 open "
            "support cases and no interaction in the last 60 days?"
        ],
    ),
    "RG-15": (
        "T1-05",
        {"engagement_type": "EmailOpen"},
        "Lexical",
        ["How many interactions are email opens?"],
    ),
    "RG-16": (
        "T4-04",
        {"campaign_type": "Awareness"},
        "Rephrase",
        [
            "For awareness campaigns, rank contact titles by their total "
            "multi-touch attribution weight."
        ],
    ),
}
# Note: Section 9.5 also lists a "Referential" class (named-entity /
# pronoun follow-up). That needs conversational context and is out of
# scope for the single-question delivery format - documented, not faked.

CONTRACT = [  # <qa>/rephrase/crm_rephrase_pairs.csv - review-only, variants only
    "natural_language_question",
    "expected_answer",
    "reference_sql",
    "reference_tables",
    "reference_fields",
    "judge_reference",
    "derivation_rationale",
]
# Must match generator/scale_pairs.py's CONTRACT / COMPANION exactly - this
# script rewrites crm_qa_pairs*.csv in place, appending the variant rows and
# filling rephrase_group_id, so the eval tool has one file for the whole
# release instead of never seeing the rephrase corpus.
FULL_CONTRACT = [
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
    "join_path_id",
    "sql_source",
    "scoring_mode",
    "answer_schema",
    "numeric_components",
    "judge_rubric_version",
    "judge_prompt_version",
    "scorer_status",
    "param_values",
    "result_hash",
    "rephrase_group_id",
    "natural_language_question",
]
MAP = [
    "pair_group_id",
    "question_id",
    "is_base",
    "variant_class",
    "result_hash",
    "natural_language_question",
]


def _read(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def generate_rephrases(profile: str) -> None:
    """Generate verified rephrase variants for an existing Q&A release."""

    _mf = json.loads((BASE / "dataset" / f"manifest_{profile}.json").read_text())
    dv, domain = _mf["dataset_version"], _mf["domain"]

    qa = resolve_qa_output_dir(BASE, profile)
    companion = _read(qa / "crm_qa_pairs_companion.csv")
    contract = {r["natural_language_question"]: r for r in _read(qa / "crm_qa_pairs.csv")}
    by_family_param = {(r["family"], r["param_values"]): r for r in companion}

    con = connect_typed(BASE / "dataset" / f"crm_{profile}.duckdb")
    build_stamp.require_fresh_db(con, _mf, BASE, profile)  # before touching output

    # Build the whole set in memory; nothing on disk changes until every
    # base is found and every variant re-verifies to the base hash.
    variants, full_variants, mapping, missing, log_entries = [], [], [], [], []
    for gid, (family, param, cls, rewordings) in GROUPS.items():
        key = (family, ";".join(f"{k}={v}" for k, v in param.items()))
        base_comp = by_family_param.get(key)
        if not base_comp:
            missing.append(f"{gid}: base {key} not in the 160")
            continue
        base = contract[base_comp["natural_language_question"]]
        gpref = _gid(gid)
        # Mutates the same dict objects held in `contract` / `companion` -
        # the base row's rephrase_group_id is filled in once validation passes.
        base["rephrase_group_id"] = gpref
        base_comp["rephrase_group_id"] = gpref
        mapping.append(
            {
                "pair_group_id": gpref,
                "question_id": base_comp["question_id"],
                "is_base": "yes",
                "variant_class": cls,
                "result_hash": base_comp["result_hash"],
                "natural_language_question": base["natural_language_question"],
            }
        )
        for i, q in enumerate(rewordings, 1):
            res = verify.run(con, base["reference_sql"])  # re-verify identity
            qid = f"{gpref}-V{i:02d}"
            log_entries.append(
                (
                    qid,
                    verify.log_payload(
                        question_id=qid,
                        tier=base_comp["tier"],
                        dataset_version=dv,
                        profile=profile,
                        sql=base["reference_sql"],
                        result=res,
                        domain=domain,
                        kind="rephrase",
                        pair_group_id=gpref,
                        base_question_id=base_comp["question_id"],
                    ),
                )
            )
            variants.append({**{k: base[k] for k in CONTRACT}, "natural_language_question": q})
            full_variants.append(
                {
                    **base,
                    "question_id": qid,
                    "natural_language_question": q,
                    "rephrase_group_id": gpref,
                    "is_release_160": "false",
                }
            )
            mapping.append(
                {
                    "pair_group_id": gpref,
                    "question_id": qid,
                    "is_base": "no",
                    "variant_class": cls,
                    "result_hash": res.result_hash,
                    "natural_language_question": q,
                }
            )

    if missing:
        raise SystemExit("MISSING BASES (nothing written):\n" + "\n".join(missing))
    bad = [
        gid
        for gid in GROUPS
        if len({m["result_hash"] for m in mapping if m["pair_group_id"] == _gid(gid)}) != 1
    ]
    if bad:
        raise SystemExit(f"rephrase hash mismatch (nothing written): {bad}")

    # Everything validated - now write. crm_qa_pairs.csv / _companion.csv are
    # rewritten IN PLACE (not rmtree'd - the 160 rows' verification_logs/
    # from scale_pairs.py live alongside them and must survive), gaining
    # rephrase_group_id on the 16 group bases and the 19 variant rows
    # appended (is_release_160=false) so the eval tool sees the whole
    # release, including rephrase groups, in one file.
    merged_pairs = list(contract.values()) + full_variants
    _write(qa / "crm_qa_pairs.csv", FULL_CONTRACT, merged_pairs)
    _write(qa / "crm_qa_pairs_companion.csv", COMPANION_FIELDS, companion)

    out = qa / "rephrase"
    if out.exists():
        shutil.rmtree(out)
    logs = out / "verification_logs"
    for name, payload in log_entries:
        verify.write_payload(logs, name, payload)
    _write(out / "crm_rephrase_pairs.csv", CONTRACT, variants)
    _write(out / "crm_rephrase_map.csv", MAP, mapping)
    _write_plan(BASE / "taxonomy" / "crm_rephrase_plan.csv")
    bases = sum(1 for m in mapping if m["is_base"] == "yes")
    print(
        f"[{profile}] rephrase: {bases} base pairs (of 160) + {len(variants)} "
        f"variants across {len(GROUPS)} groups"
    )
    print(
        f"[{profile}] {qa / 'crm_qa_pairs.csv'} now has {len(merged_pairs)} rows "
        f"(160 release + {len(full_variants)} rephrase variants)"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=["dev", "full"], default="dev")
    generate_rephrases(ap.parse_args().profile)


def _write_plan(path: Path) -> None:
    """Regenerate the taxonomy rephrase plan from GROUPS (single source of
    truth). Profile-independent and deterministic - identical on every run."""
    fields = [
        "rephrase_group_id",
        "base_family_id",
        "base_parameter",
        "variant_class",
        "variant_count",
        "expected_numeric_identity",
    ]
    rows = [
        {
            "rephrase_group_id": _gid(gid),
            "base_family_id": family,
            "base_parameter": ";".join(f"{k}={v}" for k, v in param.items()),
            "variant_class": cls,
            "variant_count": 1 + len(rewordings),
            "expected_numeric_identity": "all variants share the base pair's reference_sql, "
            "expected_answer and result_hash",
        }
        for gid, (family, param, cls, rewordings) in GROUPS.items()
    ]
    _write(path, fields, rows)


def _write(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
