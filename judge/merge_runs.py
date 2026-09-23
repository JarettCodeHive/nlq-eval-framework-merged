"""Build one scorecard from a full run plus a partial re-run.

WHY THIS EXISTS
---------------
A re-run covers only the pairs whose verdict could change, so its own scorecard
describes 55 questions rather than the domain. The number stakeholders need is
the whole domain: re-run outcomes where it covered a pair, original outcomes
everywhere else.

Neither source run is modified. A run directory is evidence — its manifest pins
the commit, input hash and argv, and `pulse_raw/` holds the untouched platform
payloads — so this writes a third, clearly-labelled directory instead.

    python -m judge.merge_runs --base <full_run> --new <rerun>

(run as a module: it imports from `judge` and `scorecard`)

The merged scorecard is always PREVIEW and never establishes a baseline: it is
assembled from two runs against two different platform states, which is exactly
the thing a baseline must not be.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from judge.contracts import ClarificationVerdict, JudgeRequest, JudgeVerdict
from judge.exact_match import ExactMatchOutcome, ExactMatchResult
from scorecard.config import report_output_dir
from scorecard.report import write_scorecard_md, write_scorecard_pdf
from scorecard.summary import (
    RunContext,
    write_question_results_csv,
    write_scorecard_summary_csv,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


class PlatformError(RuntimeError):
    """Mirror of the runner's marker for a question the platform never answered."""


def _run_dir(domain: str, run_id: str) -> Path:
    return REPO_ROOT / "release" / domain / "eval-runs" / run_id


def _load(domain: str, run_id: str) -> dict:
    path = _run_dir(domain, run_id) / "results.json"
    if not path.is_file():
        raise SystemExit(f"no results.json for {run_id} ({path})")
    return json.loads(path.read_text(encoding="utf-8"))


def _rebuild(row: dict, judge_reference: str):
    """Reconstruct the in-memory shape the scorecard writers expect."""

    pair = {
        "question_id": row["question_id"],
        "domain": row["domain"],
        "tier": row["tier"],
        "natural_language_question": row["natural_language_question"],
        "expected_answer": row["expected_answer"],
        "rephrase_group_id": row.get("rephrase_group_id"),
        "null_handling": row.get("null_handling"),
        "null_handling_detail": row.get("null_handling_detail"),
    }
    req = JudgeRequest(
        question=row["natural_language_question"],
        expected_answer=row["expected_answer"],
        judge_reference=judge_reference or row["expected_answer"],
        platform_answer=row.get("actual_answer") or "",
        generated_sql=row.get("platform_generated_sql"),
        domain=row["domain"],
        question_id=row["question_id"],
    )

    scores = row.get("judge_scores") or {}
    if scores:
        verdict: JudgeVerdict | Exception = JudgeVerdict(
            dimension_rationales=row.get("judge_rationales") or {},
            prompt_version=row.get("prompt_version") or "",
            model_version=row.get("model_version") or "",
            **scores,
        )
    elif row.get("clarification_request") or (
        row.get("exact_match_result") == ExactMatchResult.CLARIFICATION.value
    ):
        # A clarification also carries no dimension scores, but it is NOT a
        # platform error — the platform answered, with a question. Rehydrating
        # it as an error would drop its fixed score out of judge_overall and
        # move it into the platform_errors count (§2e).
        verdict = ClarificationVerdict(
            question_id=row["question_id"],
            clarification_text=row.get("clarification_text")
            or row.get("actual_answer")
            or "",
        )
    else:
        # No scores means the platform never answered, so nothing was judged.
        verdict = PlatformError(row.get("exact_match_detail") or "platform error")

    outcome = ExactMatchOutcome(
        ExactMatchResult(row["exact_match_result"]),
        row.get("exact_match_detail") or "",
        # §9.3 shape violations are a property of the pair, not of the platform,
        # and they are a named §11.1 column — dropping the flag on rehydration
        # would silently zero it in every merged or combined scorecard.
        off_contract=bool(row.get("expected_answer_off_contract")),
    )
    return pair, req, verdict, outcome


def merge(domain: str, base_id: str, new_id: str) -> Path:
    base, new = _load(domain, base_id), _load(domain, new_id)
    base_rows = {r["question_id"]: r for r in base["rows"]}
    new_rows = {r["question_id"]: r for r in new["rows"]}

    qa = (
        REPO_ROOT / "release" / domain / "qa-pairs-v0.3.0" / f"{domain}_judge_input.csv"
    )
    refs: dict[str, str] = {}
    if qa.is_file():
        with qa.open(newline="", encoding="utf-8") as fh:
            refs = {
                r["question_id"]: r.get("judge_reference", "")
                for r in csv.DictReader(fh)
            }

    merged_ids = [qid for qid in base_rows]  # base order = the domain's order
    results = []
    replaced = 0
    for qid in merged_ids:
        row = new_rows.get(qid)
        if row is not None:
            replaced += 1
        else:
            row = base_rows[qid]
        results.append(_rebuild(row, refs.get(qid, "")))

    run_id = f"merged-{base_id}-{new_id}"
    ctx = RunContext(
        run_id=run_id,
        run_timestamp_iso=datetime.now(timezone.utc).isoformat(),
        platform_version=new.get("platform_version") or "",
        dataset_version=new.get("dataset_version") or "",
        # Always PREVIEW: assembled from two runs against two different platform
        # states, which is precisely what a baseline must never be.
        scorecard_mode="PREVIEW",
        calibrated=False,
        comparison_policy=new["summary"].get("comparison_policy", ""),
        comparison_is_default=bool(new["summary"].get("comparison_is_default", True)),
        judge_temperature_enforced=bool(
            new["summary"].get("judge_temperature_enforced", True)
        ),
        judge_seed_enforced=bool(new["summary"].get("judge_seed_enforced", True)),
        provenance_note=(
            f"{replaced} of {len(results)} rows from re-run {new_id}, "
            f"the rest from {base_id}"
        ),
    )

    out = report_output_dir(domain, run_id)
    out.mkdir(parents=True, exist_ok=True)
    summary_csv = write_scorecard_summary_csv(out, results, ctx, baseline=None)
    question_csv = write_question_results_csv(out, results, ctx)
    md = write_scorecard_md(out, results, ctx, baseline=None)
    try:
        pdf = write_scorecard_pdf(out, results, ctx, baseline=None)
    except ImportError as exc:
        print(f"[merge] WARNING: PDF skipped — {exc}")
        pdf = None

    passed = sum(1 for *_, o in results if o.result is ExactMatchResult.PASS)
    eligible = sum(
        1
        for *_, o in results
        if o.result in (ExactMatchResult.PASS, ExactMatchResult.FAIL)
    )
    print(
        f"merged {len(results)} pairs — {replaced} from {new_id}, rest from {base_id}"
    )
    print(f"exact-match: {passed}/{eligible} = {100 * passed / max(1, eligible):.1f}%")
    for p in (summary_csv, question_csv, md, pdf):
        if p:
            print(f"  {p}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--domain", default="crm")
    args = ap.parse_args()
    merge(args.domain, args.base, args.new)


if __name__ == "__main__":
    main()
