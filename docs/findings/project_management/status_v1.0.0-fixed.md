# Project Management — Status (`v1.0.0-fixed`)

**Run:** `release/v1.0.0-fixed/project_management/judge/20261013T174157Z/results.json`
**Exact match:** 125 pass / 54 fail / 179 eligible — **69.83%** (target ≥90%, gap 54 failing questions, need ≥36 more passes)

The pass/fail totals are identical to the previous release (125/54),
but that's a coincidence, not a sign nothing changed — **8 individual
questions flipped status** (4 pass→fail, 4 fail→pass), confirmed by
diffing the platform's response text question by question. No
PM-specific code fix was applied this session; every difference here is
the live platform answering differently between two independent runs.

The generic sign-tolerant fallback added to `judge/exact_match.py` this
session (for a direction-bearing trend record whose change figure
prints without its sign) does not appear responsible for any PM
question passing — none of this run's newly-passing questions show
that failure shape. A separate, unrelated diagnostic-message defect
(the literal string `"None"` printed in one label-ownership message in
`judge/exact_match.py`) is confirmed still present in the code, just not
triggered by any of this run's specific failures — see the "trend
percentage omitted" category file for detail. It doesn't change any
pass/fail outcome.

## Categories affecting this domain

| Category | Count | File |
|---|---|---|
| Categorical literal mismatch | 22 | `../categories/categorical_literal_mismatch_v1.0.0-fixed.md` |
| Trend record omits the percentage change | 11 | `../categories/trend_percentage_omitted_v1.0.0-fixed.md` |
| Label pushed beyond proximity window | 1 | `../categories/label_proximity_exceeded_v1.0.0-fixed.md` |
| Scoped filter silently dropped | 1 | `../categories/dropped_filter_v1.0.0-fixed.md` |
| "non_billable" not recognized (known hard limitation) | 11 | `../categories/non_billable_substring_v1.0.0-fixed.md` |
| Number written as a word, not a digit | 1 | `../categories/number_as_word_v1.0.0-fixed.md` |
| "Overdue" counts inflated by reference-date drift | 7 | `../categories/reference_date_drift_v1.0.0-fixed.md` |
| **Total** | **54** | |

Categorical literal mismatch (22) remains the single largest block and
shares a fix lever with Sales. Reference-date drift (7) is a
scheduling/timing artifact that has only grown — the dataset-to-run gap
is now over ten weeks. `non_billable` (11) remains a known, open
limitation of this evaluation's label-matching, deliberately unfixed.
