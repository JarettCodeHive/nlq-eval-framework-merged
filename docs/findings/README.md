# Judge Failure Findings — `release/v1.0.0-fixed`

Organized by shared root cause (category-first), not by business
domain, so a fix that affects multiple domains is documented once
instead of repeated. Each category file states the mechanism once and
gives concrete examples from every domain it affects. Per-domain pages
are a thin index — pass/fail numbers and which categories apply — not
a second copy of the explanation.

## Overall

| | Eligible | Pass | Fail | Pass % |
|---|---|---|---|---|
| **Total** | **896** | **670** | **226** | **74.78%** |

| Domain | Pass | Fail | Pass % | Status page |
|---|---|---|---|---|
| CRM | 97 | 82 | 54.19% | [`crm/status_v1.0.0-fixed.md`](crm/status_v1.0.0-fixed.md) |
| Sales | 134 | 45 | 74.86% | [`sales/status_v1.0.0-fixed.md`](sales/status_v1.0.0-fixed.md) |
| Finance | 148 | 31 | 82.68% | [`finance/status_v1.0.0-fixed.md`](finance/status_v1.0.0-fixed.md) |
| Logistics | 166 | 14 | **92.22%** (above target) | [`logistics/status_v1.0.0-fixed.md`](logistics/status_v1.0.0-fixed.md) |
| Project Management | 125 | 54 | 69.83% | [`project_management/status_v1.0.0-fixed.md`](project_management/status_v1.0.0-fixed.md) |

## Categories, ranked by size

| Category | Failures | Domains affected | Fixable here? |
|---|---|---|---|
| [Breakdown/total computed via different logic than the reference](categories/breakdown_computation_mismatch_v1.0.0-fixed.md) | 95 | CRM, Sales, Finance, Logistics | No — platform query generation |
| [Categorical literal mismatch](categories/categorical_literal_mismatch_v1.0.0-fixed.md) | 55 | CRM, Sales, Finance, Project Management | No — needs a dataset re-encoding decision or platform-side filter normalization |
| [Trend record omits the percentage change](categories/trend_percentage_omitted_v1.0.0-fixed.md) | 26 | Sales, Logistics, Finance, Project Management | No — platform omission |
| ["non_billable" not recognized](categories/non_billable_substring_v1.0.0-fixed.md) | 11 | Project Management | No — known hard limitation, deliberately unfixed |
| [Scoped filter silently dropped](categories/dropped_filter_v1.0.0-fixed.md) | 7 | Finance, Logistics, Project Management | No — platform query generation |
| [Required list truncated to top-N](categories/truncated_list_v1.0.0-fixed.md) | 7 | CRM, Finance | No — platform response |
| [Internal field name used as a record label](categories/internal_label_as_record_label_v1.0.0-fixed.md) | 7 | Finance | **Partially fixed already** |
| ["Overdue" counts inflated by reference-date drift](categories/reference_date_drift_v1.0.0-fixed.md) | 7 | Project Management | No — needs a dataset-regeneration cadence decision |
| [Label pushed beyond the proximity window](categories/label_proximity_exceeded_v1.0.0-fixed.md) | 4 | CRM, Sales, Logistics, Project Management | No — presentation artifact; being watched |
| [SLA compliance rate computed from the wrong group](categories/sla_rate_wrong_cohort_v1.0.0-fixed.md) | 3 | CRM | No — platform query generation |
| [NULL rows counted together with orphan rows](categories/null_vs_orphan_v1.0.0-fixed.md) | 1 | Logistics | No — platform query generation |
| [No-data result where real data exists](categories/null_no_data_v1.0.0-fixed.md) | 1 | Logistics | No — platform query generation |
| [One-cent rounding difference](categories/one_cent_rounding_v1.0.0-fixed.md) | 1 | CRM | No — not a real defect |
| [A number written as a word, not a digit](categories/number_as_word_v1.0.0-fixed.md) | 1 | Project Management | No — this evaluation's own, deliberate design decision |
| **Total** | **226** | | |

## What's actually fixable, and by whom

- **Already fixed this session** (not in the table above — resolved,
  no remaining failures): a hyphen-matching gap and a sign-dropping gap
  in this evaluation's scorer, and a cardinality bug in one Finance
  reference query. See `finance/status_v1.0.0-fixed.md` for detail.
- **Partially fixed, residual gap remains**: the internal-label-as-
  record-label category (7 Finance questions) — see that category file.
- **The two largest remaining items need an explicit decision, not a
  patch**: categorical literal mismatch (55 questions, needs a dataset
  re-encoding or platform-side normalization decision) and reference-
  date drift (7 questions, needs a dataset-regeneration-cadence
  decision).
- **One category is a known, deliberately unfixed hard limitation**:
  "non_billable" substring confusion (11 questions) — every attempted
  fix risked turning other currently-correct label pairings into false
  passes.
- **Everything else (~177 of 226 failures)** is the live platform
  computing, omitting, or restructuring its own answer — not something
  available to change in this evaluation's scorer, dataset, or Q&A
  pairs.

## A note on run-to-run variance

Across every domain this release, re-running the identical question set
against the identical dataset produced a meaningfully different set of
failures each time — confirmed directly by diffing the platform's raw
answer text between runs, not inferred. CRM and Sales both showed **zero
overlap** in platform text for every question that failed in both the
previous and current run; Project Management had the same total fail
count in both runs but 8 different underlying questions. A few
percentage points of movement between two live runs is normal platform
noise, not evidence of a regression or improvement on this codebase's
side — keep this in mind before reading too much into any single run's
exact numbers.
