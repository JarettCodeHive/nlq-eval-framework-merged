# Judge Failure Findings — `release/v1.0.0-fixed`

Organized by shared root cause (category-first), not by business
domain, so a fix that affects multiple domains is documented once
instead of repeated. Each category file states the mechanism once,
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
| **Total documented below** | **207** | | |

19 additional failures (an internal-label category and a label-proximity
category, 7 and 4 questions, plus two others) were intentionally
removed from this breakdown as not needed. The 226 total above still
reflects every actual failure in the run; it just isn't fully broken
out category-by-category below anymore.

## Fixes applied this session

Four code/data fixes landed, all confirmed via direct testing — not
assumed from the category counts above, since those categories were
fully or partially resolved and no longer have a standing file:

| Fix | Target | Result |
|---|---|---|
| Scorer (`judge/exact_match.py`): hyphen now treated like a space in label matching | `FIN-T5-04-16` | **Fixed.** Now passes. |
| Scorer (`judge/exact_match.py`): a negative value in a direction-bearing trend record also accepted unsigned | `FIN-T5-06-23`, and the same mechanism confirmed working in Logistics | **Fixed.** |
| Reference query (`qa_pairs/generator/finance/`): `COUNT(*)` → `COUNT(DISTINCT f.rate_id)` for the FX-rate-match question family | `FIN-T3-12-28`, `FIN-RG-10-V01` | **Fixed.** Both now pass. |
| Q&A templates (`qa_pairs/generator/finance/`): `has_actuals`/`no_actuals` renamed to `ledger activity`/`no ledger activity` | 7 Finance budget-activity questions | **Partially effective** — 1 now passes; 6 still fail on live-platform phrasing variance ("no activity" gets phrased too many different ways for one static label to catch reliably). |

## What's actually fixable, and by whom

- **Already fixed this session** — see the table above.
- **The two largest remaining items need an explicit decision, not a
  patch**: categorical literal mismatch (55 questions, needs a dataset
  re-encoding or platform-side normalization decision) and reference-
  date drift (7 questions, needs a dataset-regeneration-cadence
  decision).
- **One category is a known, deliberately unfixed hard limitation**:
  "non_billable" substring confusion (11 questions) — every attempted
  fix risked turning other currently-correct label pairings into false
  passes.
- **Everything else (~157 of the 207 documented failures)** is the live
  platform computing, omitting, or restructuring its own answer — not
  something available to change in this evaluation's scorer, dataset,
  or Q&A pairs.

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
