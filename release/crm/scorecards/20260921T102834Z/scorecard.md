# Regression scorecard — `20260921T102834Z`

- run_id: `20260921T102834Z`  ·  2026-09-21T10:28:34.637720+00:00
- mode: **PREVIEW**  ·  calibrated: **False**
- platform_version: `—`  ·  dataset_version: `—`
- _PREVIEW run — not an official evaluation. Does not establish or update a baseline and must not certify a release (§10.2, §11.3)._
- exact-match comparison: `numeric=value labels=required proximity=120`

## Summary — per domain × tier

| domain | tier | questions_total | exact_match_pass | exact_match_pct | exact_match_not_applicable | judge_factual | judge_completeness | judge_format | judge_sql | judge_overall | judge_errors | platform_errors | null_handling_fail | baseline_exact_match_pct | delta_pct | regression_flag |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| crm | ALL | 3 | 0 | — | 0 | — | — | — | — | — | 0 | 3 | 0 | — | — | — |
| crm | T1 | 3 | 0 | — | 0 | — | — | — | — | — | 0 | 3 | 0 | — | — | — |

_Exact-match (§HC-3, zero numeric tolerance) and judge scores (1–5) are reported side by side and never combined into a composite (§11.3). Baseline comparison and the regression flag are domain-level; tier rows are diagnostic. `exact_match_not_applicable` counts pairs with no deterministic core — they are outside the percentage, so a rise there is not a rise in accuracy (§14.2 condition 4). `null_handling_fail` is the §9.2 T3 diagnostic and is never part of either score._
