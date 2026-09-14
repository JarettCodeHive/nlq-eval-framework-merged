# CRM Taxonomy Review Checklist

To be completed by an **independent reviewer** (not the taxonomy author,
per scope doc Section 9.4) before this taxonomy is frozen and full-scale
Q&A authoring proceeds.

Dataset in scope: `dataset-v1.0.0` (`accounts`, `contacts`, `campaigns`,
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
- [x] Rephrase groups deliver ~10% coverage — `crm_rephrase_plan.csv`
      (16 groups, 19 reworded variants, 3 classes; each base is one of the
      real 160 pairs; `generator/rephrase.py` re-verifies numeric identity
      per variant).
- [ ] Question families are business-relevant — no purely mechanical
      schema-probing questions.
- [ ] No family is ambiguous — each has exactly one defensible answer
      once its `reference_sql` is fixed.
- [ ] No CRM question duplicates a Sales question (deferred — no Sales
      taxonomy exists yet; re-check when it does, per the CRM/Sales
      boundary open item).

## Ground-truth conformance

Applies to the delivered `release/crm/qa-pairs-v<qa_version>/` package.

- [ ] Independent reviewer re-executed every `reference_sql` on a clean
      DuckDB built only from `release/crm/dataset-v1.0.0` CSVs.
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

- **OI-1** (T1 single-table tier) — **RESOLVED 2026-09-08**: T1 stays. All 32 T1 pairs are in `crm_qa_pairs.csv`.
- **OI-2** (numeric format) — **RESOLVED 2026-09-08**: golden answers are plain numbers; the eval tool's deterministic evaluator compares numerically.
- **OI-4** (NULL ingestion) — **RESOLVED 2026-09-08**: empty CSV field ingests as SQL NULL. All 32 T3 pairs are in `crm_qa_pairs.csv`.

## Sign-off

| Role | Name | Date | Decision |
|---|---|---|---|
| Taxonomy author | | | |
| Independent reviewer (must not be the author) | | | |
