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
| v1.0 | Initial spec against the `crm_dataset_v2` schema: 6 tables (`accounts`, `contacts`, `campaigns`, `contact_campaigns`, `interactions`, `support_cases`), join paths JP1–JP12, 160 pairs across 32 families (T1 32 / T2 40 / T3 32 / T4 32 / T5 24). CRM covers customer engagement, campaign attribution, and support-case / SLA behaviour; sales pipeline and revenue are excluded (Sales domain). USD only. `reference_today` = `2026-08-01`. |

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

`crm_question_family_matrix.csv` — 32 families summing (with the
`scale_pairs.py` tier cap) to exactly the Section 9.1 quota:

| Tier | Definition (Section 9.2) | Families | Quota |
|---|---|---|---:|
| T1 | single-table aggregation | T1-01…T1-07 | 32 |
| T2 | 2-table INNER JOIN + aggregate | T2-01…T2-08 | 40 |
| T3 | 2-table LEFT OUTER JOIN + NULL handling | T3-01…T3-06 | 32 |
| T4 | 3-table JOIN + aggregation | T4-01…T4-06 | 32 |
| T5 | semantic / contextual reasoning | T5-01…T5-05 | 24 |

Each family varies **N filter parameters** (1, 2, or more) drawn from the
real data — `catalog.json` maps every parameter to a `SELECT DISTINCT`
query or a fixed literal list, and `scale_pairs.py` takes the N-parameter
cartesian product (`utils.sampling.combos`) then `even_sample`s it down to
the family's explicit `quota`. Values are never invented. Each family has
an explicit per-family quota in `families.json`; `scale_pairs.py` asserts
the per-tier sum equals the Section 9.1 quota.

## 5. Answer format and scoring path

`crm_answer_format_matrix.csv`. **OI-2 resolved 2026-09-08:** golden
answers are stored as **plain numbers** (period decimal, no thousands
separator, no `$` / unit noun; money and percentages at 2 dp, counts at
0 dp). Section 9.2 fixes one scoring mode per tier; each pair records its
mode in the companion CSV `scoring_mode` column, and `utils/scoring.py`
is the reference implementation of all three:

| `scoring_mode` | Pairs | Tiers | How it is scored (`utils/scoring.py`) |
|---|---:|---|---|
| `scalar_exact` | 93 | T1, T3, T2-01/03/04/07/08 | first number extracted from the platform response, compared numerically at **zero tolerance** (non-numeric answers fall back to normalised string equality) |
| `table_exact` | 43 | all T4, T2-02/05/06 | row-for-row, cell-for-cell exact match; row order is significant (the `reference_sql` has a deterministic `ORDER BY`); numeric cells compare at zero tolerance |
| `judge_plus_exact` | 24 | all T5 | LLM-as-Judge scores the reasoning; **every numeric component of the golden answer must also appear, in order, at zero tolerance** — "the judge scores the reasoning; the scorer scores the numbers" (Section 9.2) |

Companion columns: `scoring_mode`, `answer_schema` (`shape=…; cols=name:type…;
order=…; rows=…`, derived from the executed result + `ORDER BY`),
`numeric_components` (the columns that must compare at zero tolerance),
`judge_rubric_version` / `judge_prompt_version` (set only for
`judge_plus_exact`), and `scorer_status` (`draft` until the Platform Owner
confirms the two open items below, then `approved`, then `calibrated`).

`tests/test_scoring.py` runs the evaluator against the real 160 —
identity passes, a one-step numeric change fails. `tests/test_contract.py`
checks each mode is legal for its tier. **Still to confirm with the
Platform Owner** (`open_items_confirmation_request.md`): numeric tolerance
set to 0 for our runs (spec C2), and the list/composite serialization the
Judge expects for the 24 `judge_plus_exact` answers.

## 6. Edge-case coverage

`crm_edge_case_coverage_map.csv` maps every Section 7.5 imperfection
(near-duplicate contacts, missing `attribution_weight`, engagement-point
outliers, boundary-date support cases) and each Section 7.6
missing-relationship scenario to at least one covering family.
`validate_crm.py` confirms each of these is present in the dataset before
authoring.

## 7. Rephrase coverage

`crm_rephrase_plan.csv` (generated from `generator/rephrase.py` `GROUPS`) —
**16 rephrase groups, 19 reworded variants** across **3 classes**
(Temporal, Lexical, Rephrase) per Section 9.5. Each group's base is one of
the real 160 pairs, so 16 of the 160 (~10%) are group bases. Every variant
reuses its base pair's `reference_sql` / `expected_answer` / `result_hash`
(re-verified at generation). Variant IDs are domain-prefixed
(`CRM-RG-01-V01`). The **Referential** class (Section 9.5) is intentionally
omitted: a referential variant ("...and how many of those were open?")
needs prior conversational context, which the single-turn evaluator
harness does not provide.

## 8. Open items carried into this milestone

| Item | Status | Effect |
|---|---|---|
| OI-1 (T1 single-table tier) | **Resolved 2026-09-08** — T1 stays. All 32 T1 pairs in `crm_qa_pairs.csv`. | — |
| OI-2 (numeric format) | **Resolved 2026-09-08** — plain numbers; deterministic evaluator compares numerically. | — |
| OI-4 (NULL ingestion) | **Resolved 2026-09-08** — empty field ingests as NULL. All 32 T3 pairs in `crm_qa_pairs.csv`. | — |

See `open_items_confirmation_request.md` for the routed questions.
OI-4 is resolved (empty field ingests as NULL), so T3 is no longer contingent.

## 9. Milestone deliverable checklist

This spec is complete when:

- [ ] `crm_qa_taxonomy_spec.md`, `crm_answer_format_matrix.csv`,
      `crm_edge_case_coverage_map.csv`, `crm_rephrase_plan.csv` reviewed
      against the current `schema/ddl.sql` / `schema/er.dbml`.
- [ ] `crm_taxonomy_review_checklist.md` signed by an independent
      reviewer (not the author), per Section 9.4.
- [x] Open items OI-1 / OI-2 / OI-3 / OI-4 routed and resolved (2026-09-08).
- [ ] Reviewed alongside CRM ER diagram approval, since JP1–JP12 depend
      on the DDL being correct, not just drafted.

## 10. What happens after sign-off

Sign-off is the gate that allows full-scale CRM Q&A authoring to begin —
but only after the CRM dataset itself is frozen (hashed into
`manifest.json`), since every `expected_answer` is computed by executing
`reference_sql` against the released CSVs.

T2, T4, and T5 authoring can start immediately once released CSVs land.
OI-1 and OI-4 are resolved, so T1 and T3 are shippable alongside T2/T4/T5.
