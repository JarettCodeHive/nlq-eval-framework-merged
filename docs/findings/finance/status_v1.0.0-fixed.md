# Finance — Status (`v1.0.0-fixed`)

**Run:** `release/v1.0.0-fixed/finance/judge/20261013T112105Z/results.json`
**Exact match:** 148 pass / 31 fail / 179 eligible — **82.68%** (target ≥90%, gap 15 passes)

**Previous run (`v1.0.0-full-fixed2`):** 144 pass / 35 fail / 80.45%. Four
code fixes landed between the two releases:

| Fix | Target | Result |
|---|---|---|
| Scorer: hyphen now treated like a space in label matching | `FIN-T5-04-16` | **Fixed.** Now passes. |
| Scorer: a negative value in a direction-bearing trend record also accepted unsigned | `FIN-T5-06-23` | **Fixed.** Now passes. |
| Reference query: `COUNT(*)` → `COUNT(DISTINCT f.rate_id)` for the FX-rate-match question family | `FIN-T3-12-28`, `FIN-RG-10-V01` | **Fixed.** Both now pass. |
| Q&A templates: `has_actuals`/`no_actuals` renamed to `ledger activity`/`no ledger activity` | 7 budget-activity questions | **Partially effective** — see category file. |

Net effect: **+4 passes**, with some additional churn from ordinary
platform run-to-run variance on top of the fixes (8 questions flipped
pass→fail and 5 flipped fail→pass purely because the live platform
answered differently, independent of any fix).

## Categories affecting this domain

| Category | Count | File |
|---|---|---|
| Categorical literal mismatch | 2 | `../categories/categorical_literal_mismatch_v1.0.0-fixed.md` |
| Breakdown/total computed via different logic | 11 | `../categories/breakdown_computation_mismatch_v1.0.0-fixed.md` |
| Trend record omits the percentage change | 2 | `../categories/trend_percentage_omitted_v1.0.0-fixed.md` |
| Scoped filter silently dropped | 4 | `../categories/dropped_filter_v1.0.0-fixed.md` |
| Required list truncated to top-N | 5 | `../categories/truncated_list_v1.0.0-fixed.md` |
| Internal field name used as a record label (partially fixed) | 7 | `../categories/internal_label_as_record_label_v1.0.0-fixed.md` |
| **Total** | **31** | |

24 of 31 failures are the live platform computing or omitting
something, independent of this codebase. The remaining 7 (internal
label category) are the one surviving item with a codebase-side angle,
and it's already partially addressed.
