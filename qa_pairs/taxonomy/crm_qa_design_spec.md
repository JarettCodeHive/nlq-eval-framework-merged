# CRM Q&A Design Spec — Milestone Deliverable

Milestone: "finalise Q&A taxonomy" (P1 Schema design, Wk 1–2), delivered
alongside CRM ER diagram approval and the signed CSV header spec.

**Status: DRAFT v1.0 (POC) — ready for Platform Owner DRI review. Not yet
signed off.**

Source of truth: `NLQ_Evaluation_Framework_Execution_Scope_updated.pdf`,
Sections 6, 7, 8, 9, 23. Section numbers are cited inline.

This document specifies *what should exist* (schema, join paths, question
families, tier quotas, open items). It does **not** track implementation
status — that lives in `../README.md` and `../docs/ARCHITECTURE.md`.

## Revision history

| Version | Change |
|---|---|
| v1.0 | Initial spec against the `crm_dataset_v2` schema: 6 tables (`accounts`, `contacts`, `campaigns`, `contact_campaigns`, `interactions`, `support_cases`), join paths JP1–JP12, 160 pairs across 28 families (T1 32 / T2 40 / T3 32 / T4 32 / T5 24). CRM covers customer engagement, campaign attribution, and support-case / SLA behaviour; sales pipeline and revenue are excluded (Sales domain). USD only. `reference_today` = `2026-08-01`. |

## 1. What this document is

The design-time deliverable that proves the CRM Q&A program (Track B, 160
pairs) is fully specified and ready for pair authoring — the same way the
ER diagram proves the dataset design is ready for generation. It does not
contain the 160 pairs themselves.

## 2. Schema alignment check (against `schema/ddl.sql`)

**Table set (Section 6):** `accounts`, `contacts`, `campaigns`,
`contact_campaigns`, `interactions`, `support_cases`.

**Confirmed consistent with the CRM dataset plan:**

- No denormalised engagement score on `contacts` or `accounts` — the
  engagement score is derived via `SUM(interactions.engagement_points)`,
  which forces the join for T2/T4 aggregation questions (shortcut
  prevention, Section 8.2).
- No sales/pipeline/revenue fields. Budget lives only on `campaigns`
  (`budget_amount`, USD). SLA outcomes are derived from
  `support_cases.opened_at / sla_due_at / resolved_at`, not stored.
- `contact_campaigns` is an explicit many-to-many bridge (composite PK
  `(contact_id, campaign_id)`), NOT NULL FKs on both sides.

**NOT-NULL vs nullable FK split (drives INNER vs LEFT usage):**

| Column | Nullability | Join role |
|---|---|---|
| `contacts.account_id` | nullable | LEFT (unassigned contacts / accounts without contacts) |
| `contact_campaigns.contact_id`, `.campaign_id` | NOT NULL | INNER only |
| `interactions.contact_id` | NOT NULL | INNER only |
| `interactions.account_id` | nullable | LEFT (redundant account path) |
| `interactions.campaign_id` | nullable | LEFT (NULL = organic / unattributed engagement) |
| `support_cases.account_id` | NOT NULL | INNER only |
| `support_cases.contact_id` | nullable | LEFT (account-level cases) |

## 3. Declared join paths

`crm_allowed_join_paths.csv` — JP1 through JP12. **No question family may
use a join path outside this list** (Section 8.2). Two- and three-hop
chains only; `contact_campaigns` is a bridge, so a 3-table physical join
through it (JP11) still counts as resolving a 2-entity relationship.

## 4. Question families and quota

`crm_question_family_matrix.csv` — 28 families summing (with the
`scale_pairs.py` tier cap) to exactly the Section 9.1 quota:

| Tier | Definition (Section 9.2) | Families | Quota |
|---|---|---|---:|
| T1 | single-table aggregation | T1-01…T1-06 | 32 |
| T2 | 2-table INNER JOIN + aggregate | T2-01…T2-07 | 40 |
| T3 | 2-table LEFT OUTER JOIN + NULL handling | T3-01…T3-06 | 32 |
| T4 | 3-table JOIN + aggregation | T4-01…T4-04 | 32 |
| T5 | semantic / contextual reasoning | T5-01…T5-05 | 24 |

Each family varies one (or two) filter values drawn from the real data
(`SELECT DISTINCT` — never invented). Families deliberately over-provide
values; `scale_pairs.py` consumes families in id order and stops each
tier at quota.

## 5. Answer format

`crm_answer_format_matrix.csv`. Numeric default follows Section 8.1
(period decimal separator, no thousands separator, `ROUND(...,2)` on
money) pending **OI-2** confirming this applies to `expected_answer`
strings and not only exported CSV fields.

## 6. Edge-case coverage

`crm_edge_case_coverage_map.csv` maps every Section 7.5 imperfection
(near-duplicate contacts, missing `attribution_weight`, engagement-point
outliers, boundary-date support cases) and each Section 7.6
missing-relationship scenario to at least one covering family.
`validate_crm.py` confirms each of these is present in the dataset before
authoring.

## 7. Rephrase coverage

`crm_rephrase_plan.csv` — 8 rephrase groups across 4 classes (Temporal,
Lexical, Referential, Rephrase) per Section 9.5, targeting ~10% of the
160-pair corpus once each group is expanded to its variant pairs during
authoring.

## 8. Open items carried into this milestone

| Item | Status | Effect |
|---|---|---|
| OI-1 (T1 single-table tier) | Open — T1 generated but held (`crm_t1_pairs_HELD.csv`) | 32 pairs; redistribute to T2–T5 if T1 is dropped |
| OI-2 (numeric equality) | Open — interim default applied | Every `expected_answer` string |
| OI-4 (NULL ingestion) | Open — T3 generated but held (`crm_t3_pairs_HELD.csv`) | All 32 T3 pairs; rework if empty field ≠ NULL |

See `open_items_confirmation_request.md` for the routed questions.
Because OI-4's fallback would force a re-verification of every T3 pair,
T3 must not be included in a frozen taxonomy sign-off until OI-4
resolves.

## 9. Milestone deliverable checklist

This spec is complete when:

- [ ] `crm_qa_taxonomy_spec.md`, `crm_answer_format_matrix.csv`,
      `crm_edge_case_coverage_map.csv`, `crm_rephrase_plan.csv` reviewed
      against the current `schema/ddl.sql` / `schema/er.dbml`.
- [ ] `crm_taxonomy_review_checklist.md` signed by an independent
      reviewer (not the author), per Section 9.4.
- [ ] Open items OI-1 / OI-2 / OI-4 routed and their status reflected
      here accurately — not assumed resolved.
- [ ] Reviewed alongside CRM ER diagram approval, since JP1–JP12 depend
      on the DDL being correct, not just drafted.

## 10. What happens after sign-off

Sign-off is the gate that allows full-scale CRM Q&A authoring to begin —
but only after the CRM dataset itself is frozen (hashed into
`manifest.json`), since every `expected_answer` is computed by executing
`reference_sql` against the released CSVs.

T2, T4, and T5 authoring can start immediately once released CSVs land.
T1 and T3 authoring should wait for OI-1 and OI-4 respectively, to avoid
rework — which is why this POC already segregates them into `*_HELD.csv`.
