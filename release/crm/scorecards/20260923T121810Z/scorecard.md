# Regression scorecard — `20260923T121810Z`

- run_id: `20260923T121810Z`  ·  2026-09-23T12:18:10.828739+00:00
- mode: **PREVIEW**  ·  calibrated: **False**
- platform_version: `—`  ·  dataset_version: `dataset-v1.0.0+qa-pairs-v0.3.0`
- _PREVIEW run — not an official evaluation. Does not establish or update a baseline and must not certify a release (§10.2, §11.3)._
- **judge determinism: the configured seed never reached the provider** — see `judge_seed_enforced` (§10.1 'where supported')
- exact-match comparison: `numeric=value labels=required proximity=120`

## Summary — per domain × tier

| domain | tier | questions_total | exact_match_pass | exact_match_pct | exact_match_not_applicable | exact_match_clarification | judge_factual | judge_completeness | judge_format | judge_sql | judge_overall | judge_clarifications | judge_errors | platform_errors | null_handling_fail | baseline_exact_match_pct | delta_pct | regression_flag |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| crm | ALL | 5 | 3 | 75.00 | 0 | 1 | 4.00 | 4.00 | 3.75 | 4.50 | 3.75 | 1 | 0 | 0 | 0 | — | — | — |
| crm | T1 | 5 | 3 | 75.00 | 0 | 1 | 4.00 | 4.00 | 3.75 | 4.50 | 3.75 | 1 | 0 | 0 | 0 | — | — | — |

_Exact-match (§HC-3, zero numeric tolerance) and judge scores (1–5) are reported side by side and never combined into a composite (§11.3). Baseline comparison and the regression flag are domain-level; tier rows are diagnostic. `exact_match_not_applicable` counts pairs with no deterministic core — they are outside the percentage, so a rise there is not a rise in accuracy (§14.2 condition 4). `exact_match_clarification` counts questions the platform declined to answer, asking a clarifying question instead (§2e); they are outside the percentage for the same reason, and are scored at a fixed 2.5 with no dimensions — which is why `judge_overall` is a mean over `judge_clarifications` more rows than the dimension columns are. `null_handling_fail` is the §9.2 T3 diagnostic and is never part of either score._
