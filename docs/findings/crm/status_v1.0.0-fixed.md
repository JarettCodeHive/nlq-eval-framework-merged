# CRM — Status (`v1.0.0-fixed`)

**Run:** `release/v1.0.0-fixed/crm/judge/20261014T033522Z/results.json`
**Exact match:** 97 pass / 82 fail / 179 eligible — **54.19%** (target ≥90%, gap 63 passes)

No CRM-specific code or dataset change was made this session — CRM's
failures were already 100% platform-side with nothing fixable in
`judge/exact_match.py` or the dataset. The small movement from the
previous release (80→82 failures) is confirmed pure platform
run-to-run variance, not a regression: `crm_qa_pairs.csv` is
byte-identical between releases, and every one of the 75 questions
failing in both runs has a **different** platform answer this time.

## Categories affecting this domain

| Category | Count | File |
|---|---|---|
| Categorical literal mismatch | 11 | `../categories/categorical_literal_mismatch_v1.0.0-fixed.md` |
| Breakdown/total computed via different logic | 64 | `../categories/breakdown_computation_mismatch_v1.0.0-fixed.md` |
| Label pushed beyond proximity window | 1 | `../categories/label_proximity_exceeded_v1.0.0-fixed.md` |
| Required list truncated to top-N | 2 | `../categories/truncated_list_v1.0.0-fixed.md` |
| SLA rate computed from the wrong group | 3 | `../categories/sla_rate_wrong_cohort_v1.0.0-fixed.md` |
| One-cent rounding difference | 1 | `../categories/one_cent_rounding_v1.0.0-fixed.md` |
| **Total** | **82** | |

All 82 failures are the live platform's own answer — none fixable in
this evaluation's scorer or dataset.
