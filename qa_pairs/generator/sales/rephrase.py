"""
Rephrase groups (scope doc Section 9.5): the same underlying query asked
2+ ways must return identical numerics.

Each group names a BASE pair already in the 160 (by family + parameter).
This script looks that pair up in the delivered contract + companion,
reuses its exact reference_sql / expected_answer / result_hash, and
attaches reworded variants. ~16 of the 160 are group bases (~10%).

Rewrites sales_qa_pairs.csv / sales_qa_pairs_companion.csv in place: fills
rephrase_group_id on the 16 group-base rows and appends the variant
rows (is_release_160=false), so the eval tool sees the whole release -
including rephrase groups - in one file.

Also writes (<resolved Q&A package>/rephrase/), review-only:
  sales_rephrase_pairs.csv   7-field contract - the reworded variant rows only
  sales_rephrase_map.csv     pair_group_id -> question_id, is_base, class, hash
  verification_logs/*.json

Run AFTER scale_pairs_sales.py.

    python generator/sales/rephrase.py --profile dev|full
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE.parent))
from qa_pairs.utils import build_stamp, verify  # noqa: E402
from qa_pairs.utils.duckdb_io import connect_typed  # noqa: E402
from qa_pairs.utils.output_paths import resolve_qa_output_dir  # noqa: E402

ID_PREFIX = json.loads((BASE / "generator" / "sales" / "config.json").read_text())[
    "id_prefix"
]


def _gid(group: str) -> str:
    """Domain-prefixed rephrase-group id, e.g. RG-01 -> SALES-RG-01."""
    return f"{ID_PREFIX}-{group}"


# group -> (family, {param: value}, class, [reworded variants]).
# The base value must be one the family actually sampled (index-0 values
# are always sampled by even_sample).
GROUPS = {
    "RG-01": (
        "T1-01",
        {"stage": "Lost"},
        "Lexical",
        ["What is the combined deal value across all lost deals?"],
    ),
    "RG-02": (
        "T1-06",
        {"lead_source": "ColdCall"},
        "Lexical",
        ["What's the mean lead score for leads sourced from cold calls?"],
    ),
    "RG-03": (
        "T2-08",
        {"category": "Hardware"},
        "Rephrase",
        [
            "Of hardware products' quotation lines that have been decided, "
            "what share were accepted?"
        ],
    ),
    "RG-04": (
        "T2-10",
        {"lead_source": "ColdCall"},
        "Lexical",
        ["On average, how large is a deal that originated from a cold-call lead?"],
    ),
    "RG-05": (
        "T3-01",
        {"territory": "Central"},
        "Rephrase",
        [
            "Among Central-territory leads, how many are stuck without ever "
            "turning into a deal?"
        ],
    ),
    "RG-06": (
        "T3-04",
        {"lead_status": "Contacted", "territory": "Central"},
        "Rephrase",
        [
            "How many Contacted leads based in the Central territory are "
            "still lead-only, never having advanced to a deal?",
            "Of the Contacted leads in Central territory, how many have no deal "
            "attached to them?",
        ],
    ),
    "RG-07": (
        "T3-05",
        {"lead_source": "ColdCall"},
        "Lexical",
        ["How many cold-call-sourced leads never converted into a deal?"],
    ),
    "RG-08": (
        "T4-01",
        {"stage": "Won"},
        "Rephrase",
        [
            "For Won deals, give the net quoted revenue total broken out by "
            "product category."
        ],
    ),
    "RG-09": (
        "T4-05",
        {"category": "Hardware"},
        "Rephrase",
        [
            "For hardware products, give the net quoted revenue broken out by deal stage."
        ],
    ),
    "RG-10": (
        "T5-01",
        {"stage": "Won"},
        "Lexical",
        [
            "Which three product categories brought in the most net quoted "
            "revenue among Won deals?",
            "Among Won deals, name the top three product categories by net "
            "quoted revenue.",
        ],
    ),
    "RG-11": (
        "T5-04",
        {"stage": "Won"},
        "Lexical",
        ["Among Won deals, name the top three lead sources by deal count."],
    ),
    "RG-12": (
        "T5-02",
        {"territory": "Central"},
        "Temporal",
        [
            "Which reps in the Central territory missed quota in the target "
            "period that most recently ended, and by how much?"
        ],
    ),
    "RG-13": (
        "T5-03",
        {"territory": "Central"},
        "Temporal",
        [
            "For Central-territory reps, compare total won-deal amount in "
            "the January-March 2026 period with the April-June 2026 period.",
            "Did Central-territory reps close more won-deal value in Q2 2026 "
            "than in Q1 2026?",
        ],
    ),
    "RG-14": (
        "T5-05",
        {"category": "Hardware"},
        "Temporal",
        [
            "For hardware products, how did Won-deal net quoted revenue move "
            "between the January-March 2026 and April-June 2026 periods?"
        ],
    ),
    "RG-15": (
        "T2-01",
        {"lead_source": "ColdCall"},
        "Rephrase",
        ["How many deals originated from leads that came in via a cold call?"],
    ),
    "RG-16": (
        "T1-02",
        {"lead_status": "Contacted"},
        "Lexical",
        ["How many leads currently sit in the contacted status?"],
    ),
}
# Note: Section 9.5 also lists a "Referential" class (named-entity /
# pronoun follow-up). That needs conversational context and is out of
# scope for the single-question delivery format - documented, not faked.

CONTRACT = [  # <qa>/rephrase/sales_rephrase_pairs.csv - review-only, variants only
    "natural_language_question",
    "expected_answer",
    "reference_sql",
    "reference_tables",
    "reference_fields",
    "judge_reference",
    "derivation_rationale",
]
# Must match generator/scale_pairs_sales.py's CONTRACT / COMPANION exactly -
# this script rewrites sales_qa_pairs*.csv in place, appending the variant
# rows and filling rephrase_group_id, so the eval tool has one file for the
# whole release instead of never seeing the rephrase corpus.
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
    """Generate verified rephrase variants for an existing Sales Q&A release."""

    _mf = json.loads((BASE / "dataset" / f"manifest_sales_{profile}.json").read_text())
    dv, domain = _mf["dataset_version"], _mf["domain"]

    qa = resolve_qa_output_dir(BASE, profile, "sales")
    companion = _read(qa / "sales_qa_pairs_companion.csv")
    contract = {
        r["natural_language_question"]: r for r in _read(qa / "sales_qa_pairs.csv")
    }
    by_family_param = {(r["family"], r["param_values"]): r for r in companion}

    con = connect_typed(BASE / "dataset" / f"sales_{profile}.duckdb")
    build_stamp.require_fresh_db(
        con, _mf, BASE, profile, "sales", BASE / "dataset" / "sales" / profile
    )  # before touching output

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
            variants.append(
                {**{k: base[k] for k in CONTRACT}, "natural_language_question": q}
            )
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
        if len({m["result_hash"] for m in mapping if m["pair_group_id"] == _gid(gid)})
        != 1
    ]
    if bad:
        raise SystemExit(f"rephrase hash mismatch (nothing written): {bad}")

    # Everything validated - now write. sales_qa_pairs.csv / _companion.csv
    # are rewritten IN PLACE (not rmtree'd - the 160 rows' verification_logs/
    # from scale_pairs_sales.py live alongside them and must survive),
    # gaining rephrase_group_id on the group bases and the variant rows
    # appended (is_release_160=false) so the eval tool sees the whole
    # release, including rephrase groups, in one file.
    merged_pairs = list(contract.values()) + full_variants
    _write(qa / "sales_qa_pairs.csv", FULL_CONTRACT, merged_pairs)
    _write(qa / "sales_qa_pairs_companion.csv", COMPANION_FIELDS, companion)

    out = qa / "rephrase"
    if out.exists():
        shutil.rmtree(out)
    logs = out / "verification_logs"
    for name, payload in log_entries:
        verify.write_payload(logs, name, payload)
    _write(out / "sales_rephrase_pairs.csv", CONTRACT, variants)
    _write(out / "sales_rephrase_map.csv", MAP, mapping)
    _write_plan(BASE / "taxonomy" / "sales_rephrase_plan.csv")
    bases = sum(1 for m in mapping if m["is_base"] == "yes")
    print(
        f"[{profile}] rephrase: {bases} base pairs (of 160) + {len(variants)} "
        f"variants across {len(GROUPS)} groups"
    )
    print(
        f"[{profile}] {qa / 'sales_qa_pairs.csv'} now has {len(merged_pairs)} rows "
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
        w = csv.DictWriter(
            fh, fieldnames=fields, quoting=csv.QUOTE_MINIMAL, lineterminator="\n"
        )
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
