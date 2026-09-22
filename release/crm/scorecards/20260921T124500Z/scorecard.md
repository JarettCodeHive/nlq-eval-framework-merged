# Regression scorecard — `20260921T124500Z`

- run_id: `20260921T124500Z`  ·  2026-09-21T12:45:00.139186+00:00
- mode: **PREVIEW**  ·  calibrated: **False**
- platform_version: `—`  ·  dataset_version: `—`
- _PREVIEW run — not an official evaluation. Does not establish or update a baseline and must not certify a release (§10.2, §11.3)._
- **judge determinism: the configured seed never reached the provider** — see `judge_seed_enforced` (§10.1 'where supported')
- exact-match comparison: `numeric=value labels=required proximity=120`

## Summary — per domain × tier

| domain | tier | questions_total | exact_match_pass | exact_match_pct | exact_match_not_applicable | judge_factual | judge_completeness | judge_format | judge_sql | judge_overall | judge_errors | platform_errors | null_handling_fail | baseline_exact_match_pct | delta_pct | regression_flag |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| crm | ALL | 160 | 61 | 38.12 | 0 | 2.62 | 3.29 | 3.42 | 3.59 | 3.23 | 0 | 0 | 0 | — | — | — |
| crm | T1 | 32 | 24 | 75.00 | 0 | 4.00 | 4.31 | 3.91 | 4.22 | 4.11 | 0 | 0 | 0 | — | — | — |
| crm | T2 | 40 | 14 | 35.00 | 0 | 2.40 | 3.25 | 3.52 | 3.90 | 3.27 | 0 | 0 | 0 | — | — | — |
| crm | T3 | 32 | 23 | 71.88 | 0 | 3.88 | 4.69 | 4.28 | 4.88 | 4.43 | 0 | 0 | 0 | — | — | — |
| crm | T4 | 32 | 0 | 0.00 | 0 | 1.09 | 1.91 | 2.78 | 2.19 | 1.99 | 0 | 0 | 0 | — | — | — |
| crm | T5 | 24 | 0 | 0.00 | 0 | 1.54 | 2.00 | 2.29 | 2.42 | 2.06 | 0 | 0 | 0 | — | — | — |

_Exact-match (§HC-3, zero numeric tolerance) and judge scores (1–5) are reported side by side and never combined into a composite (§11.3). Baseline comparison and the regression flag are domain-level; tier rows are diagnostic. `exact_match_not_applicable` counts pairs with no deterministic core — they are outside the percentage, so a rise there is not a rise in accuracy (§14.2 condition 4). `null_handling_fail` is the §9.2 T3 diagnostic and is never part of either score._
