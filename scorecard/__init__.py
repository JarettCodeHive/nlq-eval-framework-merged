"""Regression scorecard — Execution Spec §11.

Two machine-readable CSVs plus a human PDF per evaluation run:

- ``scorecard_summary.csv``   — one row per domain, plus per-tier rows within
  domain (§11.1). Carries the baseline comparison and the regression flag.
- ``question_results.csv``    — one row per evaluated question (§11.2). Carries
  ``platform_generated_sql`` for every question and the per-dimension judge
  scores + rationale.

Exact-match accuracy and judge scores are reported side by side and never
blended into a composite (§11.3).
"""

from __future__ import annotations

from scorecard.baseline import (
    BaselineExists,
    RegressionComparison,
    compare_to_baseline,
    establish_baseline,
    load_baseline,
)
from scorecard.summary import (
    RunContext,
    aggregate,
    write_question_results_csv,
    write_scorecard_summary_csv,
)

__all__ = [
    "RunContext",
    "aggregate",
    "write_scorecard_summary_csv",
    "write_question_results_csv",
    "BaselineExists",
    "RegressionComparison",
    "compare_to_baseline",
    "establish_baseline",
    "load_baseline",
]
