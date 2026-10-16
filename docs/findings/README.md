# Judge Failure Findings — `release/v1.0.0-fixed`

Organized by shared root cause (category-first), Each category file states the mechanism once,
gives concrete examples from every domain it affects, and says exactly
where to find the underlying question/answer data by `question_id`.
There are no per-domain folders — everything lives under `categories/`.

## Overall

| | Eligible | Pass | Fail | Pass % |
|---|---|---|---|---|
| **Total** | **896** | **670** | **226** | **74.78%** |

| Domain | Pass | Fail | Pass % |
|---|---|---|---|
| CRM | 97 | 82 | 54.19% |
| Sales | 134 | 45 | 74.86% |
| Finance | 148 | 31 | 82.68% |
| Logistics | 166 | 14 | **92.22%** (above target) |
| Project Management | 125 | 54 | 69.83% |

## Categories, ranked by size

| Category | Failures | Domains affected | Fixable here? |
|---|---|---|---|
| [Breakdown/total computed via different logic than the reference](categories/breakdown_computation_mismatch_v1.0.0-fixed.md) | 95 | CRM, Sales, Finance, Logistics | No — platform query generation |
| [Categorical literal mismatch](categories/categorical_literal_mismatch_v1.0.0-fixed.md) | 55 | CRM, Sales, Finance, Project Management | No — needs a dataset re-encoding decision or platform-side filter normalization |
| [Trend record omits the percentage change](categories/trend_percentage_omitted_v1.0.0-fixed.md) | 26 | Sales, Logistics, Finance, Project Management | No — platform omission |
| ["non_billable" not recognized](categories/non_billable_substring_v1.0.0-fixed.md) | 11 | Project Management | No — known hard limitation, deliberately unfixed |
| [Scoped filter silently dropped](categories/dropped_filter_v1.0.0-fixed.md) | 7 | Finance, Logistics, Project Management | No — platform query generation |
| ["Overdue" counts inflated by reference-date drift](categories/reference_date_drift_v1.0.0-fixed.md) | 7 | Project Management | No — needs a dataset-regeneration cadence decision |
| [SLA compliance rate computed from the wrong group](categories/sla_rate_wrong_cohort_v1.0.0-fixed.md) | 3 | CRM | No — platform query generation |
| [NULL rows counted together with orphan rows](categories/null_vs_orphan_v1.0.0-fixed.md) | 1 | Logistics | No — platform query generation |
| [One-cent rounding difference](categories/one_cent_rounding_v1.0.0-fixed.md) | 1 | CRM | No — not a real defect |
| [A number written as a word, not a digit](categories/number_as_word_v1.0.0-fixed.md) | 1 | Project Management | No — this evaluation's own, deliberate design decision |
