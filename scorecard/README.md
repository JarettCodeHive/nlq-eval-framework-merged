# Scorecard

Regression scorecard generation.

Expected outputs:

- Summary CSV with run, platform, dataset, domain, tier, exact-match, judge, and
  regression fields.
- Question-level drill-down CSV with expected answer, actual answer,
  exact-match result, generated SQL, judge scores, and rationale.
- Human-readable PDF summary.

Exact-match accuracy and judge scores must be reported side by side. Do not
combine them into a single composite score.
