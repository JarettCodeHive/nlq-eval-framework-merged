# Regression scorecard — `merged-20260921T124500Z-20260921T153322Z`

- run_id: `merged-20260921T124500Z-20260921T153322Z`  ·  2026-09-21T16:38:28.024498+00:00
- mode: **PREVIEW**  ·  calibrated: **False**
- platform_version: `—`  ·  dataset_version: `—`
- _PREVIEW run — not an official evaluation. Does not establish or update a baseline and must not certify a release (§10.2, §11.3)._
- **judge determinism: the configured seed never reached the provider** — see `judge_seed_enforced` (§10.1 'where supported')
- exact-match comparison: `numeric=value labels=required proximity=120`
- assembled: 55 of 160 rows from re-run 20260921T153322Z, the rest from 20260921T124500Z

## Summary — per domain × tier

| domain | tier | questions_total | exact_match_pass | exact_match_pct | exact_match_not_applicable | judge_factual | judge_completeness | judge_format | judge_sql | judge_overall | judge_errors | platform_errors | null_handling_fail | baseline_exact_match_pct | delta_pct | regression_flag |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| crm | ALL | 160 | 88 | 55.00 | 0 | 3.34 | 3.64 | 3.58 | 3.81 | 3.59 | 0 | 0 | 0 | — | — | — |
| crm | T1 | 32 | 26 | 81.25 | 0 | 4.25 | 4.31 | 3.97 | 4.38 | 4.23 | 0 | 0 | 0 | — | — | — |
| crm | T2 | 40 | 30 | 75.00 | 0 | 3.98 | 4.15 | 3.85 | 4.38 | 4.09 | 0 | 0 | 0 | — | — | — |
| crm | T3 | 32 | 32 | 100.00 | 0 | 5.00 | 5.00 | 4.41 | 5.00 | 4.85 | 0 | 0 | 0 | — | — | — |
| crm | T4 | 32 | 0 | 0.00 | 0 | 1.09 | 1.94 | 2.84 | 2.22 | 2.02 | 0 | 0 | 0 | — | — | — |
| crm | T5 | 24 | 0 | 0.00 | 0 | 1.88 | 2.38 | 2.50 | 2.62 | 2.34 | 0 | 0 | 0 | — | — | — |

_Exact-match (§HC-3, zero numeric tolerance) and judge scores (1–5) are reported side by side and never combined into a composite (§11.3). Baseline comparison and the regression flag are domain-level; tier rows are diagnostic. `exact_match_not_applicable` counts pairs with no deterministic core — they are outside the percentage, so a rise there is not a rise in accuracy (§14.2 condition 4). `null_handling_fail` is the §9.2 T3 diagnostic and is never part of either score._
