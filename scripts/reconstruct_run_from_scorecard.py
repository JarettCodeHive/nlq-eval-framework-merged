"""Rebuild a run's `results.json` from the scorecard CSVs it produced.

WHEN YOU NEED THIS
------------------
An evaluation run writes two things: its own evidence under
`release/<domain>/eval-runs/<run_id>/` (gitignored — `results.json`,
`prompts_log.jsonl`, `pulse_raw/`) and its scorecard under
`release/<domain>/scorecards/<run_id>/` (tracked). If the evidence is lost but
the scorecard survives, this recovers enough to re-render scorecards and to feed
`main.py score`.

WHAT IS AND IS NOT RECOVERED
----------------------------
`question_results.csv` is the §11.2 drill-down, so per-question results, judge
scores, failure details and generated SQL all come back intact — everything the
scorecard arithmetic needs.

Lost for good, because the CSVs never carried it:
  - per-dimension judge rationales (only the combined string survives)
  - prompt_version / model_version per verdict
  - the raw platform payloads in `pulse_raw/`, and `run_manifest.json`

So the output is good enough to re-derive a scorecard and NOT good enough to
audit a judgment. It is written under a `-RECONSTRUCTED` run id, carries a
`reconstructed_from` field, and drops a README beside itself, so it can never be
mistaken for the original evidence.

    python -m scripts.reconstruct_run_from_scorecard \\
        --domain crm --scorecard merged-20260921T124500Z-20260921T153322Z
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_DIMENSION_COLUMNS = {
    "factual_correctness": "judge_factual",
    "completeness": "judge_completeness",
    "format_adherence": "judge_format",
    "sql_plausibility": "judge_sql",
}


def _as_bool(text: str | None) -> bool:
    return str(text or "").strip().lower() in ("true", "1", "yes")


def _as_float(text: str | None) -> float | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def rebuild_rows(question_csv: Path) -> list[dict]:
    """One results.json row per §11.2 question row."""

    rows: list[dict] = []
    with question_csv.open(newline="", encoding="utf-8") as handle:
        for record in csv.DictReader(handle):
            row: dict = {
                "question_id": record["question_id"],
                "domain": record["domain"],
                "tier": record["tier"],
                "natural_language_question": record["natural_language_question"],
                "expected_answer": record["expected_answer"],
                "actual_answer": record.get("actual_answer") or "",
                "exact_match_result": record["exact_match_result"],
                "exact_match_detail": record.get("exact_match_detail") or "",
                "expected_answer_off_contract": _as_bool(
                    record.get("expected_answer_off_contract")
                ),
                "platform_generated_sql": record.get("platform_generated_sql") or None,
                "null_handling": record.get("null_handling") or None,
                "null_handling_detail": record.get("null_handling_detail") or None,
                "rephrase_group_id": record.get("rephrase_group_id") or None,
            }

            scores = {
                dimension: _as_float(record.get(column))
                for dimension, column in _DIMENSION_COLUMNS.items()
            }
            if all(value is not None for value in scores.values()):
                row["judge_scores"] = {k: int(v) for k, v in scores.items()}
                # The per-dimension rationales were flattened into one string by
                # the CSV; the same string is attributed to each dimension and
                # labelled, rather than inventing four different ones.
                combined = record.get("judge_rationale") or "recovered from scorecard CSV"
                row["judge_rationales"] = {k: combined for k in _DIMENSION_COLUMNS}
                row["judge_rationale"] = combined
                row["judge_overall"] = _as_float(record.get("judge_overall"))
                row["prompt_version"] = "unrecoverable"
                row["model_version"] = "unrecoverable"
            elif record["exact_match_result"] == "clarification":
                row["clarification_request"] = True
                row["judge_scores"] = None
            elif record.get("platform_error"):
                row["platform_error"] = record["platform_error"]
            elif record.get("judge_error"):
                row["judge_error"] = record["judge_error"]
            rows.append(row)
    return rows


def rebuild_summary(summary_csv: Path) -> dict:
    """The provenance block, from the §11.1 domain roll-up row."""

    with summary_csv.open(newline="", encoding="utf-8") as handle:
        rows = [r for r in csv.DictReader(handle) if r.get("tier") == "ALL"]
    if not rows:
        return {}
    first = rows[0]
    return {
        "comparison_policy": first.get("comparison_policy") or "",
        "comparison_is_default": True,
        "judge_temperature_enforced": _as_bool(first.get("judge_temperature_enforced")),
        "judge_seed_enforced": _as_bool(first.get("judge_seed_enforced")),
        # Calibration state is not a §11.1 column. A run that had been calibrated
        # would have said so in its own results.json; absent that, assume not,
        # because assuming calibrated would let a recovered run masquerade as
        # release-eligible.
        "calibrated": False,
    }


def reconstruct(domain: str, scorecard_id: str, *, repo_root: Path | None = None) -> Path:
    root = repo_root or REPO_ROOT
    source = root / "release" / domain / "scorecards" / scorecard_id
    question_csv = source / "question_results.csv"
    summary_csv = source / "scorecard_summary.csv"
    for path in (question_csv, summary_csv):
        if not path.is_file():
            raise SystemExit(f"missing {path}")

    rows = rebuild_rows(question_csv)
    summary = rebuild_summary(summary_csv)

    with summary_csv.open(newline="", encoding="utf-8") as handle:
        roll_up = next(
            (r for r in csv.DictReader(handle) if r.get("tier") == "ALL"), {}
        )

    run_id = f"{scorecard_id}-RECONSTRUCTED"
    target = root / "release" / domain / "eval-runs" / run_id
    target.mkdir(parents=True, exist_ok=True)

    payload = {
        "run_id": run_id,
        "run_timestamp_iso": roll_up.get("run_timestamp_iso") or "",
        "platform_version": roll_up.get("platform_version") or "",
        "dataset_version": roll_up.get("dataset_version") or "",
        "scorecard_mode": "PREVIEW",
        "judge": "llm",
        "mode": "combined",
        "pulse_mode": "live",
        "reconstructed_from": str(source.relative_to(root)),
        "reconstruction_note": (
            "Rebuilt from the §11.2 question CSV after the original eval-run "
            "directory was lost. Scorecard arithmetic is faithful; per-dimension "
            "rationales, prompt/model versions and raw platform payloads are not "
            "recoverable. Not usable as audit evidence."
        ),
        "summary": summary,
        "rows": rows,
    }
    (target / "results.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    (target / "RECONSTRUCTED.md").write_text(
        f"""# Reconstructed run — not original evidence

Rebuilt by `scripts/reconstruct_run_from_scorecard.py` from
`{source.relative_to(root)}` because the original eval-run directory was lost.

Faithful: per-question results, judge dimension scores, failure details,
generated SQL, null-handling diagnostics, tier and domain attribution — i.e.
everything the scorecard computes from.

Not recoverable: per-dimension judge rationales (one combined string is reused
and labelled), `prompt_version` / `model_version`, `prompts_log.jsonl`,
`pulse_raw/`, `run_manifest.json`.

`calibrated` is recorded as **false** — calibration state is not a §11.1 column,
and assuming otherwise would let this run look release-eligible when nothing
attests that it was.
""",
        encoding="utf-8",
    )
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--domain", default="crm")
    parser.add_argument(
        "--scorecard",
        required=True,
        help="the scorecard directory name under release/<domain>/scorecards/",
    )
    args = parser.parse_args()
    target = reconstruct(args.domain, args.scorecard)
    rows = json.loads((target / "results.json").read_text())["rows"]
    print(f"reconstructed {len(rows)} row(s) -> {target}")
    print(f"now: python main.py score --run {args.domain}={target.name}")


if __name__ == "__main__":
    main()
