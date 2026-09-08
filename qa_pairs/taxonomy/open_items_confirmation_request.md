# Written Confirmation Request — CRM Open Items (OI-1, OI-2, OI-4)

Source: `NLQ_Evaluation_Framework_Execution_Scope_updated.pdf`, Section 23
(Open Items Requiring Decision). These three items block or risk CRM Q&A
authoring. This file is a draft ready to send to the named owners; it is
not itself a resolution.

Dataset in scope: `crm_dataset_v2` — tables `accounts`, `contacts`,
`campaigns`, `contact_campaigns`, `interactions`, `support_cases`.
`reference_today` = `2026-08-01`.

---

## OI-1 — T1 single-table tier vs. shortcut-prevention rule

**Owner:** Engagement Lead → Platform Owner
**Blocking:** CRM T1 pair authoring

**The conflict, as written in the scope doc:**
- Section 9.1 sets T1 ("single-table aggregation") at 20% of all pairs —
  32 of CRM's 160.
- Section 8.2 states: *"for tiers T2 through T5, the schema must make it
  impossible to answer the question correctly from a single table…
  Minimum 2-hop join depth applies to T2–T5 questions."*

Section 8.2 scopes the join-depth rule to T2–T5, which reads as a
built-in exemption for T1 — but Section 23 flags this as "directly
contradictory" with no recorded resolution, so we are not treating the
exemption as confirmed.

**Question for the Platform Owner:**
Is T1 a genuine single-table control tier (no join required, exempt from
Section 8.2), at the 20% / 32-pair CRM quota? Or is the T1 quota
redistributed into T2–T5, with every tier requiring at least one join?

**Interim handling:** CRM T1 pairs are generated (32, full quota) but
written to `qa_pairs/<profile>/crm_t1_pairs_HELD.csv` and the T1 seeds to
`crm_qa_pairs_seed_HELD.csv`. Either ruling costs a merge, not a rewrite
(Risk R-10 mitigation).

---

## OI-2 — Numeric equality semantics for `expected_answer`

**Owner:** Evaluation Engineer → Platform Owner DRI
**Blocking:** affects every `expected_answer` value generated

**The gap, as written in the scope doc:**
No section defines decimal precision, rounding, or string formatting for
numeric answers. Section 9.4 requires exact-match with zero tolerance,
which is undefined without a canonical numeric format. Concrete CRM
cases:
- `crm_t1_pairs_HELD.csv` budget totals: is `25549190.91` correct, or
  `25,549,190.91`, or `25549190.9`? (DuckDB `ROUND(SUM(x),2)` currently
  emits `25549190.91`.)
- engagement-point counts render as bare integers (`28731`).
- `crm_t5_pairs.csv` percentage-change values (`-83.2`) — precision and
  rounding method are undefined.

**Question for the Platform Owner:**
Confirm the canonical format for numeric `expected_answer` values:
- Fixed decimal places per measure (currency/budget = 2dp, counts = 0dp)?
- Thousands separators absent (per the Section 8.1 CSV contract) — does
  that rule apply to `expected_answer` strings, or only to exported CSV
  data fields?
- Rounding method (banker's vs round-half-up) for derived
  averages/percentages?

**Interim handling:** the POC uses the Section 8.1 CSV convention (period
decimal, no thousands separator, `ROUND(...,2)` on money) as the default
— see `crm_answer_format_matrix.csv`.

---

## OI-4 — NULL representation on ingestion

**Owner:** Data Engineering Lead → Platform Owner DRI
**Blocking:** before any T3 pair ships; affects every T3 (LEFT JOIN) pair

**The risk, as written in the scope doc:**
Golden CSVs represent NULL as an empty field (Section 8.1). If the
platform's ingestion reads an empty CSV field as an empty string rather
than SQL NULL, every T3 answer computed against true-NULL semantics will
not match what the platform treats as "no match" — the whole T3 tier
(32 CRM pairs) is at risk of a systematic mismatch that is a platform
ingestion issue, not a dataset or taxonomy defect.

CRM T3 families that depend on this: accounts with no contacts, accounts
with no direct interactions, contacts with no interactions, contacts not
in any campaign, campaigns with no attributed interactions, contacts with
no support cases (`crm_t3_pairs_HELD.csv`, families T3-01…T3-06).

**Question for the Platform Owner:**
When the platform ingests a CSV with an empty field for a nullable
column, is that value interpreted as SQL NULL (excluded from
joins/aggregations) or as an empty string (a real value subject to
string comparison)?

**Interim handling:** T3 families and pairs are drafted against true-NULL
semantics and held in `crm_t3_pairs_HELD.csv`; every T3 family is marked
in `crm_edge_case_coverage_map.csv` as OI-4-dependent so they can be
isolated for rework without touching approved T1/T2/T4/T5 content.

---

## Summary for whoever sends these

| ID | Send to | Blocks | Interim posture |
|---|---|---|---|
| OI-1 | Platform Owner (via Engagement Lead) | T1 quota validity | 32 T1 pairs generated, held in `*_HELD.csv` |
| OI-2 | Platform Owner DRI (via Evaluation Engineer) | `expected_answer` format | Default to Section 8.1 CSV format |
| OI-4 | Platform Owner DRI (via Data Engineering Lead) | All 32 T3 pairs | Generated against true-NULL, held as contingent |

None of these block T2, T4, or T5 authoring — those depend on none of a
single-table exemption, answer-format edge cases, or NULL/LEFT-JOIN
ingestion semantics.
