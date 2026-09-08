# CRM Q&A Pair POC — Architecture & How It Works

This document is the design reference: what each part does, how data
flows, what the guarantees are, and how to extend it. For the current
build state (what is / isn't done) see `../README.md`. For the tier-to-
schema mapping see `../taxonomy/crm_qa_taxonomy_spec.md`.

---

## 1. Where this sits

The NLQ Evaluation Framework has five tracks (scope doc §5):

| Track | Component | Relationship to this POC |
|---|---|---|
| A | Golden synthetic datasets | **Input** — delivered as `crm_dataset_v2` |
| **B** | **Q&A evaluation pair sets** | **This POC** — produces the answer key |
| C | LLM-as-Judge module | Consumes `judge_reference` + `expected_answer` |
| D | Regression scorecard | Consumes `expected_answer` for exact-match scoring |
| E | Evaluation Orchestrator | Deferred (design only) |

**Track B produces the answer key.** For each of the five domains it must
deliver 750+ (target 800) natural-language questions, each with a
deterministic ground-truth answer and the metadata to audit it. This POC
implements the CRM slice (160 pairs) end-to-end to prove the approach
before the Week 3–7 authoring effort.

The **one rule** that shapes the whole design (scope doc §9.6):

> `expected_answer` is computed by executing `reference_sql` against the
> exported golden CSVs in DuckDB — never hand-typed, never computed from
> generator state, never taken from what the platform returned.

Everything below exists to enforce that rule mechanically and leave an
audit trail.

---

## 2. Component map

```
qa_pairs_poc/
├── data/crm_dataset_v2/        INPUT. The frozen Track-A deliverable. Read-only.
│                               6 CSVs per profile + crm_ddl.sql + crm.json + base.json.
│
├── schema/                     CONTRACT. ddl.sql (column names pairs bind to) +
│                               er.dbml (the diagram, version-controlled as text).
│
├── taxonomy/                   DESIGN. What questions exist, before any code:
│   ├── crm_qa_taxonomy_spec.md         tier -> schema narrative
│   ├── crm_question_family_matrix.csv  28 families, param, tier quota
│   ├── crm_allowed_join_paths.csv      JP1-JP12 - the only joins a family may use
│   ├── crm_answer_format_matrix.csv    how each answer shape serializes to a string
│   └── crm_edge_case_coverage_map.csv  each imperfection -> the family that exercises it
│
├── generator/                  CODE. schema + data -> verified pairs:
│   ├── base.json               seed, reference_today, imperfection rates (from the bundle)
│   ├── generate_crm.py         Step 1 - stage the bundle into a working dataset
│   ├── validate_crm.py         Step 2 - integrity gate on the staged dataset
│   ├── author_qa_pairs.py      Step 3a - hand-written seed pairs (1 per family)
│   ├── scale_pairs.py          Step 3b - templated pairs at full quota
│   └── templates/t{1..5}/*.sql.j2   one tiny SQL template per question family
│
├── dataset/                    GENERATED (git-ignored). Step 1 output:
│   └── <profile>/*.csv , crm_<profile>.duckdb , manifest_<profile>.json
│
└── qa_pairs/                   GENERATED (tracked). Step 3 output:
    └── <profile>/
        ├── crm_t{1..5}_pairs[_HELD].csv   templated pairs, per tier
        ├── crm_qa_pairs_seed[_HELD].csv   hand-written seeds
        └── verification_logs/<question_id>.json   one per question
```

`<profile>` is `dev` (1% scale, fast iteration) or `full` (delivery
scale). **Only `full` output is deliverable** (scope doc §7.3).

---

## 3. Data flow

```
 data/crm_dataset_v2/<profile>/*.csv          (frozen golden dataset)
        │
        │  ── generate_crm.py ──────────────────────────────────────────────
        │     • copy the 6 CSVs into dataset/<profile>/
        │     • CREATE the DDL in a fresh crm_<profile>.duckdb, COPY the CSVs
        │       in parent-before-child order so every FK resolves on load
        │     • write manifest_<profile>.json: sha256 per file, row/col counts,
        │       seed=42, reference_today=2026-08-01, imperfection rates
        ▼
 dataset/<profile>/*.csv , crm_<profile>.duckdb , manifest_<profile>.json
        │
        │  ── validate_crm.py ──────────────────────────────────────────────
        │     GATE (non-zero exit blocks the pipeline):
        │     • every declared FK resolves (no orphan child rows)
        │     • every INNER JOIN path returns >= 1 row
        │     • every LEFT  JOIN path leaves >= 1 unmatched parent row
        │     • the four controlled imperfections are present (full profile)
        ▼
        │  ── author_qa_pairs.py  (hand)   and   scale_pairs.py  (templated)
        │     ┌──────────────────────────────────────────────────────────┐
        │     │ for each candidate pair:                                 │
        │     │   1. obtain reference_sql                                │
        │     │        seed  -> a hand-written string in the PAIRS list  │
        │     │        scaled -> render templates/tN/TN-MM.sql.j2 with a │
        │     │                  real value pulled from the CSVs         │
        │     │   2. execute it in DuckDB against dataset/<profile>/     │
        │     │   3. classify the result:                               │
        │     │        1x1 non-null   -> expected_answer = str(value)    │
        │     │        1 row, N cols  -> "a | b | c"                     │
        │     │        M rows         -> "r1a | r1b; r2a | r2b; ..."     │
        │     │        0 rows         -> BLOCKED (blocked_zero_rows)     │
        │     │        NULL scalar    -> BLOCKED (blocked_null_scalar)   │
        │     │        SQL error      -> BLOCKED (error)                 │
        │     │   4. write verification_logs/<question_id>.json always   │
        │     │   5. BLOCKED pairs are logged but NOT written to the CSV │
        │     └──────────────────────────────────────────────────────────┘
        ▼
 qa_pairs/<profile>/*.csv  +  qa_pairs/<profile>/verification_logs/*.json
        │
        │  ── (downstream, not in this POC) ────────────────────────────────
        │     • independent reviewer re-runs every reference_sql on a clean
        │       DuckDB from the released files only, confirms result_hash
        │     • Track C judge gets question + expected_answer + judge_reference
        │       (NOT reference_sql — that would let it cheat on SQL plausibility)
        │     • Track D scorecard compares platform answer == expected_answer
        ▼
```

---

## 4. The two authoring paths

Both paths produce **identical CSV and log formats** and run the same
verify-and-log machinery. They differ only in where `reference_sql`
comes from.

### 4a. Seed pairs — `author_qa_pairs.py`

A `PAIRS` list of hand-written dicts, **one per family across all five
tiers**. Each dict carries the full question text, a hand-written
`reference_sql`, and the metadata. Used to:

- prove every family can be expressed against the real schema
- cover question shapes a template cannot (CTEs, `CASE` period
  comparisons, composite at-risk logic — see T5 seeds)
- give reviewers a small, readable set to check first

Output: `crm_qa_pairs_seed.csv` (T2/T4/T5) and
`crm_qa_pairs_seed_HELD.csv` (T1/T3).

### 4b. Templated pairs — `scale_pairs.py`

Each **question family** has one tiny SQL template
(`templates/t2/T2-01.sql.j2`):

```sql
SELECT COUNT(s.case_id) AS result
FROM support_cases s INNER JOIN accounts a ON s.account_id = a.account_id
WHERE a.region = '{{ region }}';
```

The script:

1. Builds a **value catalog** from the CSVs — `SELECT DISTINCT region
   FROM accounts` etc. Values are never invented; they come from the
   delivered data.
2. Renders each family's template once per value (family `T2-07` /
   `T4-04` take two params and render the cross product).
3. Executes, classifies, logs — same as the seed path.
4. Assigns `question_id = CRM-T{tier}-{family}-{seq}` where `seq` runs
   across the whole tier.
5. Writes the tier's CSV, stopping at the Section 9.1 quota. Families are
   consumed in id order, so families deliberately over-provide values and
   the tier cap decides how many of the last family are used.

Why templates: hand-writing 160 queries drifts — inconsistent aliases,
`BETWEEN` vs half-open date ranges, `LIMIT` without a deterministic
`ORDER BY`. A template fixes the query *shape* once per family; the
author only supplies the filter value.

---

## 5. The HELD mechanism

A `_HELD` file holds pairs that are **fully generated and verified but
deliberately withheld from delivery** because an unresolved decision
could invalidate them. Scope doc Risk R-10: *author the pairs but hold
them in a separate file so a ruling either way costs one merge, not a
rewrite.*

| File | Held on | If the ruling goes the other way |
|---|---|---|
| `crm_t1_pairs_HELD.csv`, T1 seeds | **OI-1** — is a single-table tier even allowed? (§8.2 vs §9.1) | 32 pairs redistribute into T2–T5 |
| `crm_t3_pairs_HELD.csv`, T3 seeds | **OI-4** — does the platform read an empty CSV field as NULL or `""`? | every T3 LEFT-JOIN count re-verified against platform join behaviour |

Non-`_HELD` files (`crm_t2/t4/t5_pairs.csv`, `crm_qa_pairs_seed.csv`)
have no open-item dependency and are "clear to ship."

---

## 6. Guarantees

| Guarantee | How it is enforced |
|---|---|
| **Answers are never hand-typed** | Only the verify-and-log function writes `expected_answer`; it only ever writes a query result. |
| **Answers match the data as delivered** | SQL runs against `dataset/<profile>/*.csv`, which `generate_crm.py` copied verbatim from the bundle — imperfections (near-duplicates, NULLs, outliers, boundary dates) included. |
| **Reproducible** | `generate_crm.py` does no RNG, no wall-clock, no network. `manifest_<profile>.json` records the sha256 of every CSV; a clean re-run regenerates identical hashes. `reference_today` is a constant (`2026-08-01`), never `now()`. |
| **Auditable** | One `verification_logs/<id>.json` per question with the exact SQL, execution status, row count, `result_hash`, and the answer produced. A reviewer re-runs the SQL and checks the hash. |
| **No shortcut answers** | T2–T5 templates and seeds only use join paths declared in `crm_allowed_join_paths.csv`; no family relies on a denormalized column that would let one table answer a multi-hop question. |
| **Schema-bound** | Every `reference_sql` names real `schema/ddl.sql` columns; `validate_crm.py` + execution against the real CSVs catch any drift immediately. |

---

## 7. Failure modes and how they surface

| Situation | What happens |
|---|---|
| `reference_sql` has a typo / references a missing column | DuckDB raises → log `execution_status: "error"` → pair excluded from the CSV |
| Filter value matches no rows | 0 rows → `blocked_zero_rows` → excluded (a question with no answer is not shippable) |
| Aggregate over an empty set returns NULL | `blocked_null_scalar` → excluded |
| A tier ends up below quota | `scale_pairs.py` prints `<-- UNDER QUOTA`; add family values or a new family |
| Dataset changes upstream | `manifest` hashes change; every answer must be re-verified (this is why the schema/dataset must be *frozen* before authoring) |

---

## 8. Extending it

**Add a question to an existing family:** the family already renders one
pair per distinct value — add the value to the data, or widen the
family's `SELECT DISTINCT`. No code change.

**Add a family:** write `templates/tN/TN-MM.sql.j2`, add one spec dict to
`tN_specs()` in `scale_pairs.py` (template path, param name, question
text, `reference_tables`, `reference_fields`, `judge_reference`,
`derivation_rationale`), and a row in
`taxonomy/crm_question_family_matrix.csv`. Add a hand seed to
`author_qa_pairs.py` too.

**Add a tier:** unlikely — T1–T5 are fixed by scope doc §9.2.

**Add a domain (Sales, Finance, PM, Logistics):** copy the folder, swap
`data/`, `schema/`, `taxonomy/`, and the template + spec sets. The
`generate_crm` / `validate` / `author` / `scale` structure is
domain-agnostic; only the table names, value catalog, and family
definitions change. Enforce cross-domain question uniqueness (scope doc
§9.4) with a normalized-text hash across all domains' CSVs.

---

## 9. What this POC does not do (real Track B adds)

- SQLFluff lint on every `reference_sql` (§9.4/§9.6)
- Cross-corpus duplicate-question hash check (§9.4)
- Independent review — here the author and verifier are the same process;
  §9.4 requires them to differ (checklist:
  `../taxonomy/crm_taxonomy_review_checklist.md`)
- Rephrase-group variants — planned in
  `../taxonomy/crm_rephrase_plan.csv` (8 groups, ~10% target, §9.5), not
  yet generated as pairs
- Ambiguity set for questions with more than one defensible reading
- Git LFS + a `qa-v1.0.0` tag on the delivered set
- The four remaining domains (Sales, Finance, PM, Logistics)

## 10. Governance documents

| Doc | Purpose |
|---|---|
| `../taxonomy/crm_qa_design_spec.md` | Milestone deliverable — the design, for Platform-Owner sign-off |
| `../taxonomy/crm_taxonomy_review_checklist.md` | Independent-reviewer checklist (author ≠ reviewer) |
| `../taxonomy/open_items_confirmation_request.md` | OI-1 / OI-2 / OI-4 written questions, routed to owners |
| `../taxonomy/crm_rephrase_plan.csv` | The ~10% rephrase-group plan |
