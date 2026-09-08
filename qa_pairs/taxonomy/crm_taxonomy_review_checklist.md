# CRM Taxonomy Review Checklist

To be completed by an **independent reviewer** (not the taxonomy author,
per scope doc Section 9.4) before this taxonomy is frozen and full-scale
Q&A authoring proceeds.

Dataset in scope: `crm_dataset_v2` (`accounts`, `contacts`, `campaigns`,
`contact_campaigns`, `interactions`, `support_cases`).

## Design conformance

- [ ] Follows Section 9.2 tier definitions exactly — no tier redefined.
- [ ] Per-tier counts match the Section 9.1 quota: T1=32, T2=40, T3=32,
      T4=32, T5=24, total=160. (Enforced by `scale_pairs.py`, which caps
      each tier; re-verify after any family edit.)
- [ ] Every `reference_sql` uses only tables/columns present in
      `schema/ddl.sql`. (Enforced by execution against the real CSVs —
      any drift produces a `blocked` verification log.)
- [ ] T2–T5 families use only join paths declared in
      `crm_allowed_join_paths.csv` (JP1–JP12) and cannot be answered from
      one denormalized table (Section 8.2 shortcut-prevention).
- [ ] T3 families exercise real missing-relationship scenarios, not just
      nullable FKs (Section 7.6). Confirmed by `validate_crm.py`: each
      LEFT JOIN path leaves ≥ 1 unmatched parent row.
- [ ] Every T5 family resolves to a deterministic numeric core computed
      by a single SQL query (Section 9.2: "T5 is not a harder SQL tier").
- [ ] Relative time language ("last quarter", "right now", "recent") is
      grounded against `reference_today = 2026-08-01` in every
      `derivation_rationale`, never wall-clock time.
- [ ] Each Section 7.5 imperfection (near-duplicates, missing values,
      outliers, boundary dates) and each missing-relationship scenario is
      mapped to ≥ 1 covering family — `crm_edge_case_coverage_map.csv`.
- [ ] Rephrase groups are planned toward ~10% coverage —
      `crm_rephrase_plan.csv` (8 groups; variants not yet generated).
- [ ] Question families are business-relevant — no purely mechanical
      schema-probing questions.
- [ ] No family is ambiguous — each has exactly one defensible answer
      once its `reference_sql` is fixed.
- [ ] No CRM question duplicates a Sales question (deferred — no Sales
      taxonomy exists yet; re-check when it does, per the CRM/Sales
      boundary open item).

## Ground-truth conformance (per delivered `qa_pairs/full/`)

- [ ] Independent reviewer re-executed every `reference_sql` on a clean
      DuckDB built only from the released `data/crm_dataset_v2/full` CSVs.
- [ ] Every re-execution's `result_hash` matches the committed
      `verification_logs/<question_id>.json`.
- [ ] No shipped CSV row has `execution_status` other than `ok` in its
      log; every `blocked_*` question is absent from the CSVs.
- [ ] `expected_answer` in each CSV row equals `generated_expected_answer`
      in its log.
- [ ] Clean-room re-run of `generate_crm.py --profile full` reproduces
      identical `manifest_full.json` file hashes.

## ER diagram

- [ ] `schema/er.dbml` matches `schema/ddl.sql` (table set, PK/FK,
      cardinality, INNER vs LEFT annotations).
- [ ] `schema/er.dbml` pasted into https://dbdiagram.io and the exported
      PDF committed alongside the source (manual step — not producible
      from this repo).

## Open items affecting this checklist

- **OI-1** (T1 single-table tier) — UNRESOLVED. T1 pairs are generated
  but held (`crm_t1_pairs_HELD.csv`). Do not include T1 in a frozen
  taxonomy sign-off until OI-1 is confirmed in writing.
- **OI-2** (numeric equality format) — UNRESOLVED. `crm_answer_format_matrix.csv`
  uses a default assumption; re-verify once confirmed.
- **OI-4** (NULL ingestion semantics) — UNRESOLVED. All T3 families are
  held as contingent (`crm_t3_pairs_HELD.csv`). Do not include T3 in a
  frozen sign-off until OI-4 is confirmed in writing.

## Sign-off

| Role | Name | Date | Decision |
|---|---|---|---|
| Taxonomy author | | | |
| Independent reviewer (must not be the author) | | | |
