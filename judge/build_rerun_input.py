"""Build a re-run input CSV containing only the pairs worth asking again.

WHY
---
A full CRM domain is 160 live questions — roughly half an hour of platform time.
After a fix that can only affect some of them, re-running everything wastes most
of that. This selects the subset whose verdict could actually change.

WHAT IS EXCLUDED, AND WHY
-------------------------
Pairs that passed: nothing to learn.

Pairs whose failure is a **question-side specification ambiguity** — the ground
truth and the platform computed genuinely different things (a different
aggregate, or a different join path). Re-uploading data or re-profiling a schema
cannot change that; only rewriting the question can. Re-running them burns
platform time to observe the same failure.

Clarification requests are deliberately KEPT. They look hopeless but they are
not: every one was caused by the platform's schema profile missing an enum value
that exists in the data, and a re-upload re-profiles the schema. Whether that
fixes the sampling is exactly what the next run should measure, and at ~11
questions it is the cheapest information in the set.

    python judge/build_rerun_input.py --run <run_id>
    python judge/build_rerun_input.py --run <run_id> --include-ambiguous

The output carries the same columns as the full input, so it drops straight into
`main.py score --input-csv`.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# A failure is treated as question-side ambiguity when the ground truth uses an
# aggregate the platform never used, or reaches the fact table through a
# different join path. Both are properties of the two SQL statements, not of the
# data, so neither can be resolved by re-uploading.
_AGG = re.compile(r"\b(SUM|COUNT|AVG|MIN|MAX)\s*\(", re.I)


def _aggregates(sql: str) -> set[str]:
    return {m.upper() for m in _AGG.findall(sql or "")}


def _joins_contacts(sql: str) -> bool:
    return bool(re.search(r"\bcontacts\b", sql or "", re.I))


def is_spec_ambiguity(reference_sql: str, platform_sql: str) -> bool:
    # No platform SQL means the platform never ran a query — a clarification
    # request or an error. Comparing against an empty string makes every
    # aggregate look "missing", which would classify all 11 clarifications as
    # spec ambiguity and drop exactly the pairs most worth asking again.
    if not (platform_sql or "").strip():
        return False
    metric = bool(_aggregates(reference_sql) - _aggregates(platform_sql))
    join_path = _joins_contacts(reference_sql) and not _joins_contacts(platform_sql)
    return metric or join_path


def build(
    run_dir: Path, input_csv: Path, output: Path, *, include_ambiguous: bool
) -> tuple[int, int]:
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    rows = results["rows"]

    with input_csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        pairs = {row["question_id"]: row for row in reader}

    keep: list[str] = []
    skipped_ambiguous: list[str] = []
    for row in rows:
        if row["exact_match_result"] == "pass":
            continue
        qid = row["question_id"]
        pair = pairs.get(qid)
        if pair is None:  # input CSV no longer carries this pair
            continue
        if not include_ambiguous and is_spec_ambiguity(
            pair.get("reference_sql", ""), row.get("platform_generated_sql") or ""
        ):
            skipped_ambiguous.append(qid)
            continue
        keep.append(qid)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        # Input order, so a run log still climbs tiers.
        for qid in [r["question_id"] for r in rows if r["question_id"] in set(keep)]:
            writer.writerow(pairs[qid])

    print(f"re-run set      : {len(keep)} pair(s) -> {output}")
    if skipped_ambiguous:
        print(
            f"skipped         : {len(skipped_ambiguous)} pair(s) whose failure is a "
            "question-side spec ambiguity that a data re-upload cannot fix"
        )
        print(f"                  {', '.join(sorted(skipped_ambiguous))}")
    print(
        f"estimated time  : ~{len(keep) * 30 / 4 / 60:.0f} min at 30s/question, "
        "concurrency 4"
    )
    return len(keep), len(skipped_ambiguous)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True, help="run_id to take failures from")
    ap.add_argument("--domain", default="crm")
    ap.add_argument(
        "--input-csv",
        type=Path,
        default=None,
        help="the full judge input the run used; defaults to the Q&A release",
    )
    ap.add_argument("--output", type=Path, default=None)
    ap.add_argument(
        "--include-ambiguous",
        action="store_true",
        help="also re-run pairs whose failure is a known spec ambiguity",
    )
    args = ap.parse_args()

    run_dir = REPO_ROOT / "release" / args.domain / "eval-runs" / args.run
    if not (run_dir / "results.json").is_file():
        raise SystemExit(f"no results.json under {run_dir}")

    qa_dir = REPO_ROOT / "release" / args.domain / "qa-pairs-v0.3.0"
    input_csv = args.input_csv or qa_dir / f"{args.domain}_judge_input.csv"
    output = args.output or qa_dir / f"{args.domain}_rerun_{args.run}.csv"
    build(run_dir, input_csv, output, include_ambiguous=args.include_ambiguous)


if __name__ == "__main__":
    main()
