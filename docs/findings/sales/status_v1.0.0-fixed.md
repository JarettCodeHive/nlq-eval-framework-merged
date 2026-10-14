# Sales — Status (`v1.0.0-fixed`)

**Run:** `release/v1.0.0-fixed/sales/judge/20261013T102604Z/results.json`
**Exact match:** 134 pass / 45 fail / 179 eligible — **74.86%** (target ≥90%, gap 15.14 points)

No Sales-specific code or dataset change was made this session. The
shift from the previous release (42→45 failures) is confirmed pure
platform run-to-run variance: every one of the 13 questions whose
verdict changed between runs has a **different** platform answer; zero
have identical text with a different verdict.

## Categories affecting this domain

| Category | Count | File |
|---|---|---|
| Categorical literal mismatch | 20 | `../categories/categorical_literal_mismatch_v1.0.0-fixed.md` |
| Breakdown/total computed via different logic | 19 | `../categories/breakdown_computation_mismatch_v1.0.0-fixed.md` |
| Trend record omits the percentage change | 5 | `../categories/trend_percentage_omitted_v1.0.0-fixed.md` |
| Label pushed beyond proximity window | 1 | `../categories/label_proximity_exceeded_v1.0.0-fixed.md` |
| **Total** | **45** | |

All 45 failures are the live platform's own answer, SQL generation, or
response structure — none fixable in this evaluation's scorer or
dataset. 20 of them (categorical literal mismatch) share a fix lever
with Project Management — see that category file.
