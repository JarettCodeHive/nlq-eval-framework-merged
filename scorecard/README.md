# Scorecard

Regression scorecard generation — Execution Spec §11.

## Outputs (written per run into `release/<domain>/scorecards/<run_id>/`)

- **`scorecard_summary.csv`** (§11.1) — one row per domain plus a per-tier
  breakout within domain. Carries `run_id`, `run_timestamp_iso`,
  `platform_version`, `dataset_version`, deterministic accuracy
  (`exact_match_pass` / `exact_match_pct`), the four mean judge dimension
  scores + `judge_overall`, and the baseline comparison
  (`baseline_exact_match_pct`, `delta_pct`, `regression_flag`).
- **`question_results.csv`** (§11.2) — one row per evaluated question:
  expected vs actual answer, `exact_match_result`, `platform_generated_sql`
  (logged for *every* question), per-dimension judge scores, and the rationale.
- **`scorecard.md`** — GitHub-renderable summary for PRs.
- **`scorecard.pdf`** — human deliverable (§11.3).

Exact-match accuracy and judge scores are reported side by side and never
combined into a composite score (§11.3).

## Baseline & regression (§11.3)

`scorecard/baselines/<platform_version>.json` holds the immutable accuracy
baseline. It is written exactly once — the first RELEASE run for a platform
version — then made read-only. Every later RELEASE run compares against it;
a domain that drops `REGRESSION_DELTA_PP` (−5.0) percentage points or more
trips `regression_flag`. The comparison is domain-level; tier moves are
diagnostic only.

PREVIEW runs (any run without `--release`, or with the mock judge / no
calibration marker) never establish or update a baseline.

## Modules

| File | Role |
|---|---|
| `summary.py` | aggregation + `scorecard_summary.csv` + `question_results.csv` |
| `baseline.py` | immutable baseline store, delta + regression flag |
| `report.py` | Markdown + PDF renders |
