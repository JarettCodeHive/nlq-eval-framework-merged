# CRM Q&A Taxonomy Spec (POC)

Status: DRAFT — OI-1 / OI-2 / OI-3 / OI-4 **resolved 2026-09-08**
(see `open_items_confirmation_request.md`). All 160 pairs are shippable;
the held-file split for T1/T3 was removed.

Coverage: 32 families over engagement, campaigns, support/SLA, and
campaign attribution. SLA compliance (`T1-07`, `T2-08`, `T4-03`),
attribution weight with NULL handling (`T4-04`), and grouped 3-table
aggregation (all of T4) are implemented. `derivation_rationale` is fixed
per family (it describes the method, not the parameter value). See
`../docs/REMEDIATION.md` for the open review items.

Source of truth: `NLQ_Evaluation_Framework_Execution_Scope_updated.pdf`
Sections 6-9, 23. This spec maps the fixed T1-T5 tiers (Section 9.2) onto
the `dataset-v1.0.0` CRM schema. It does not redefine the tiers.

## 1. Scope

CRM tables (`schema/ddl.sql`): `accounts`, `contacts`, `campaigns`,
`contact_campaigns` (contacts<->campaigns many-to-many bridge),
`interactions`, `support_cases`. CRM covers customer engagement, campaign
attribution, and support-case / SLA behaviour. No sales pipeline or
revenue (that is the Sales domain). USD only. `reference_today` =
`2026-08-01`. Quarter-over-quarter questions compare **Q1 2026**
(`2026-01-01` .. `2026-04-01`) with **Q2 2026** (`2026-04-01` ..
`2026-07-01`) — the two most recent *complete* calendar quarters (the
data ends `2026-07-28`, so Q3 is incomplete and excluded).

Declared join paths: `crm_allowed_join_paths.csv` (JP1-JP12). No family
may use a join outside this list (Section 8.2 shortcut prevention).

## 2. Quota (Section 9.1)

T1=32, T2=40, T3=32, T4=32, T5=24, total=160, across 32 families
(`crm_question_family_matrix.csv`). Each family has an **explicit
`param_count` quota**; `scale_pairs.py` asserts the per-tier sum equals
the Section 9.1 quota and uses `even_sample()` to spread values when a
family's quota is below the number of distinct values available.

## 3. Tier-by-tier

All five tiers are generated to full quota by `scale_pairs.py` into
`crm_qa_pairs.csv` (all 160).

- **T1 (32) — 7 families.** OI-1 resolved: T1 stays a single-table
  control tier. Campaign budget by type, campaign count by status, case
  count by priority / category, interaction count by engagement type,
  account count by region, SLA-met case count by priority.
- **T2 (40) — 8 families.** JP1/JP4/JP5/JP6/JP7. Includes T2-08 SLA
  compliance % by industry.
- **T3 (32) — 6 families.** OI-4 resolved: empty CSV field ingests as
  NULL, so the LEFT-JOIN "missing relationship" answers hold.
  `validate_crm.py` confirms each LEFT path has unmatched parent rows.
- **T4 (32) — 6 families, all `GROUP BY`** over a real dimension
  (channel / priority / category / member_status / status /
  engagement_type). Every family joins 3 tables that each do work
  (dimension / measure / filter). T4-04 exercises the
  `attribution_weight` NULL imperfection.
- **T5 (24) — 5 families:** ranked campaign types, open backlog,
  composite at-risk, Q1-vs-Q2 engagement change, best regional trend.
  Account engagement is always derived through
  `accounts -> contacts -> interactions` (the ERD's primary path), never
  the nullable direct `interactions.account_id`. T5-04/05 do not assume a
  growth direction.

## 4. Answer format

`crm_answer_format_matrix.csv`. OI-2 resolved: golden answers are stored
as **plain numbers** — `28731`, `23184584.00`, `23.21` — with no currency
symbol, thousands separator, or unit noun. Section 9.2 fixes one scoring
mode per tier, recorded per pair in the companion `scoring_mode` column
and implemented by `utils/scoring.py`: `scalar_exact` (T1/T3 + five T2
families — first-number numeric compare at zero tolerance), `table_exact`
(all T4 + T2-02/05/06 — row/cell-exact, order significant), and
`judge_plus_exact` (all T5 — LLM Judge on the reasoning plus every numeric
component matched exactly). See `../docs/REMEDIATION.md` "Review 5".

## 5. Edge-case coverage

`crm_edge_case_coverage_map.csv` maps every Section 7.5 imperfection
(near-duplicates, missing values, outliers, boundary dates) and the
Section 7.6 missing-relationship scenarios to at least one family.

## 6. What this spec does not do

It does not redefine T1-T5.
