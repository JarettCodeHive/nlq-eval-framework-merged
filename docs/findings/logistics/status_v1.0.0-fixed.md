# Logistics — Status (`v1.0.0-fixed`)

**Run:** `release/v1.0.0-fixed/logistics/judge/20261013T160913Z/results.json`
**Exact match:** 166 pass / 14 fail / 180 eligible — **92.22%** — already above the ≥90% target.

**Previous run:** 164 pass / 16 fail / 91.11%. A scorer fix (a negative
numeric in a direction-bearing trend record also accepted printed
unsigned) resolved the previous release's "a change value loses its
sign" category outright — that category has **zero members now** and
is removed from this index. The specific row it targeted
(`LOG-T5-05-12`) failed again anyway this run, but for an unrelated
reason — the platform's answer this time dropped the percentage
entirely instead of printing the dollar change unsigned, moving it into
the "trend percentage omitted" category below. Confirmed by diffing
`actual_answer` text: this is live-platform variance, not a regression
in the fix, which is independently verified working via this session's
regression harness.

Net count moved from 16 to 14 (−2) via 4 rows improving and 2 new rows
appearing — all 6 confirmed as platform run-to-run variance, unrelated
to any code change.

## Categories affecting this domain

| Category | Count | File |
|---|---|---|
| Breakdown/total computed via different logic | 1 | `../categories/breakdown_computation_mismatch_v1.0.0-fixed.md` |
| Trend record omits the percentage change | 8 | `../categories/trend_percentage_omitted_v1.0.0-fixed.md` |
| Label pushed beyond proximity window | 1 | `../categories/label_proximity_exceeded_v1.0.0-fixed.md` |
| Scoped filter silently dropped | 2 | `../categories/dropped_filter_v1.0.0-fixed.md` |
| No-data result where real data exists | 1 | `../categories/null_no_data_v1.0.0-fixed.md` |
| NULL rows counted together with orphan rows | 1 | `../categories/null_vs_orphan_v1.0.0-fixed.md` |
| **Total** | **14** | |

No category here is a dataset/Q&A-pair defect — every `expected_answer`
checked against `reference_sql`/`logistics_semantics.md` is internally
consistent.
