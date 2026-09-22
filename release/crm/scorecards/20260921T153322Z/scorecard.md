# Regression scorecard — `20260921T153322Z`

- run_id: `20260921T153322Z`  ·  2026-09-21T15:33:22.568158+00:00
- mode: **PREVIEW**  ·  calibrated: **False**
- platform_version: `—`  ·  dataset_version: `—`
- _PREVIEW run — not an official evaluation. Does not establish or update a baseline and must not certify a release (§10.2, §11.3)._
- **judge determinism: the configured seed never reached the provider** — see `judge_seed_enforced` (§10.1 'where supported')
- exact-match comparison: `numeric=value labels=required proximity=120`

## Summary — per domain × tier

| domain | tier | questions_total | exact_match_pass | exact_match_pct | exact_match_not_applicable | judge_factual | judge_completeness | judge_format | judge_sql | judge_overall | judge_errors | platform_errors | null_handling_fail | baseline_exact_match_pct | delta_pct | regression_flag |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| crm | ALL | 55 | 27 | 49.09 | 0 | 3.25 | 3.53 | 3.35 | 3.82 | 3.49 | 0 | 0 | 0 | — | — | — |
| crm | T1 | 8 | 2 | 25.00 | 0 | 2.00 | 2.25 | 2.75 | 2.50 | 2.38 | 0 | 0 | 0 | — | — | — |
| crm | T2 | 26 | 16 | 61.54 | 0 | 3.42 | 3.69 | 3.50 | 4.04 | 3.66 | 0 | 0 | 0 | — | — | — |
| crm | T3 | 9 | 9 | 100.00 | 0 | 5.00 | 5.00 | 4.33 | 5.00 | 4.83 | 0 | 0 | 0 | — | — | — |
| crm | T4 | 4 | 0 | 0.00 | 0 | 1.75 | 3.00 | 3.00 | 3.00 | 2.69 | 0 | 0 | 0 | — | — | — |
| crm | T5 | 8 | 0 | 0.00 | 0 | 2.75 | 2.88 | 2.50 | 3.50 | 2.91 | 0 | 0 | 0 | — | — | — |

_Exact-match (§HC-3, zero numeric tolerance) and judge scores (1–5) are reported side by side and never combined into a composite (§11.3). Baseline comparison and the regression flag are domain-level; tier rows are diagnostic. `exact_match_not_applicable` counts pairs with no deterministic core — they are outside the percentage, so a rise there is not a rise in accuracy (§14.2 condition 4). `null_handling_fail` is the §9.2 T3 diagnostic and is never part of either score._
