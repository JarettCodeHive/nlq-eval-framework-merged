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
│   ├── crm_question_family_matrix.csv  32 families, param, tier quota
│   ├── crm_allowed_join_paths.csv      JP1-JP12 - the only joins a family may use
│   ├── crm_answer_format_matrix.csv    how each answer shape serializes to a string
│   └── crm_edge_case_coverage_map.csv  each imperfection -> the family that exercises it
│
├── utils/                       DOMAIN-AGNOSTIC core:
│   ├── verify.py               execute SQL + serialize + deterministic log
│   ├── serialization.py        THE ONLY place a result becomes an answer string
│   ├── sql.py                  sqlstr() literal quoting + Jinja env factory
│   ├── sampling.py             even_sample() + N-parameter combos()
│   ├── duckdb_io.py            typed-DuckDB connect + distinct()
│   └── labels.py               generic label lookup (CRM map: generator/labels.json)
│
├── generator/                  CRM SPECIFICS - data files + thin drivers:
│   ├── base.json               seed, reference_today, imperfection rates (from the bundle)
│   ├── config.json             domain / id prefix / tier quotas / resolved-OI record
│   ├── catalog.json            param name -> SQL listing its allowed values
│   ├── families.json           32 question families - DECLARATIVE, the source of truth
│   ├── labels.json             CRM enum token -> business phrase (applied to question text only)
│   ├── generate_crm.py         Step 1 - stage the bundle into a TYPED working dataset
│   ├── validate_crm.py         Step 2 - integrity gate on the staged dataset
│   ├── scale_pairs.py          Step 3 - 160 pairs from families.json + templates
│   ├── author_qa_pairs.py      Step 3b - seed FIXTURES (review only, not the 160)
│   ├── rephrase.py             Step 3c - 16 rephrase groups + map (Section 9.5 companion)
│   ├── gen_family_matrix.py    regenerate the taxonomy matrix CSV from families.json
│   └── templates/t{1..5}/*.sql.j2   one tiny SQL template per question family
│
├── tests/                      pytest: contract / templates / ground-truth / scoring / rephrase / sqlfluff
├── pyproject.toml              requires-python 3.11, ruff + black config
│                               (CI: <repo>/.github/workflows/qa-pairs-poc.yml)
│
├── dataset/                    GENERATED (git-ignored except manifests). Step 1 output:
│   └── <profile>/*.csv , crm_<profile>.duckdb , manifest_<profile>.json
│
├── qa_pairs/<profile>/         GENERATED (tracked). Step 3 output - THE RELEASE:
│   ├── crm_qa_pairs.csv               7-field contract, all 160
│   ├── crm_qa_pairs_companion.csv     question_id / tier / family / join_path / scoring_mode / answer_schema / hash
│   └── verification_logs/<question_id>.json   one per question (160)
│
└── fixtures/<profile>/         GENERATED (tracked). Step 3b - review only, NOT the 160:
    └── crm_seed_fixtures.csv , crm_seed_fixtures_companion.csv , verification_logs/
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
        │       into TYPED tables in parent-before-child order (FKs resolve on load;
        │       DECIMAL stays DECIMAL, so zero-tolerance exact-match holds)
        │     • write manifest_<profile>.json: sha256 per file, DDL hash,
        │       toolchain (python + duckdb versions), canonical dataset-v1.0.0 tag
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
        │  ── scale_pairs.py  (templated, the 160)   +   author_qa_pairs.py  (seed fixtures)
        │     ┌──────────────────────────────────────────────────────────────┐
        │     │ scale_pairs: for each family (explicit quota):                │
        │     │   • pick values with even_sample() if quota < available      │
        │     │   • render templates/tN/TN-MM.sql.j2  (StrictUndefined -      │
        │     │     a missing template var is a hard failure)                 │
        │     │   • execute against the TYPED crm_<profile>.duckdb            │
        │     │   • expected_answer via serialization.py ONLY:               │
        │     │       money   -> Decimal, 2 places always                    │
        │     │       count   -> plain int                                   │
        │     │       percent -> 2 places                                    │
        │     │       1 row   -> "a | b | c"   ;   M rows -> "r1; r2"        │
        │     │   • blocked (error / 0 rows / NULL scalar) -> *.blocked.json, │
        │     │     NOT numbered, NOT in the CSV                              │
        │     │   • survivors numbered CRM-T{n}-{family}-{seq} in a 2nd pass  │
        │     │   • non-zero exit if any tier misses its quota               │
        │     └──────────────────────────────────────────────────────────────┘
        ▼
 qa_pairs/<profile>/crm_qa_pairs.csv (+_companion)  +  verification_logs/*.json
 fixtures/<profile>/crm_seed_fixtures.csv (+_companion)  +  verification_logs/*.json
        │
        │  ── pytest tests/ ───────────────────────────────────────────────
        │     7-field contract · tier counts == 32/40/32/32/24 · normalized-text
        │     uniqueness · every template var provided · re-execute every SQL and
        │     match the committed result_hash
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

## 4. The two paths

### 4a. The release — `scale_pairs.py` (160 pairs)

A family is **one entry in `generator/families.json`** plus **one SQL
template**:

```json
{"family": "T2-01", "tier": "T2", "template": "t2/T2-01.sql.j2",
 "params": ["region"], "quota": 5, "join_path": "JP7",
 "question": "How many support cases belong to {region}-region accounts?",
 "tables": "support_cases, accounts", "fields": "...",
 "judge": "...", "rationale": "..."}
```

```sql
-- templates/t2/T2-01.sql.j2
-- string params go through the sqlstr filter, which emits the quotes and
-- doubles any embedded apostrophe. Never write '{{ region }}' by hand.
SELECT COUNT(s.case_id) AS case_count
FROM support_cases s INNER JOIN accounts a ON s.account_id = a.account_id
WHERE a.region = {{ region | sqlstr }};
```

Adding a family is those two edits — no Python change. `scale_pairs.py`:

1. Loads `families.json`; asserts the per-tier `quota` sums are
   32/40/32/32/24.
2. Builds a **value catalog** from the CSVs — `SELECT DISTINCT region
   FROM accounts` etc. Never invented.
3. Per family: `even_sample()` picks `quota` values evenly across the
   range (endpoints included) — not the first N. Renders with
   `StrictUndefined`; `check_template_vars()` fails the run if a template
   uses a variable the family doesn't list in `params`.
4. Executes against the **typed** `crm_<profile>.duckdb`. `expected_answer`
   comes only from `serialization.py`.
5. Blocked candidates (SQL error / 0 rows / NULL scalar) get a
   `{family}__{params}.blocked.json` log and are dropped.
6. Survivors are numbered `CRM-T{n}-{family}-{seq}` in a **second pass**,
   so a dropped candidate can never reuse an id.
7. The whole release is built **in memory**; nothing is written and the
   previous release is not deleted until every per-tier quota is met — a
   failed run leaves no partial answer key. Then it writes
   `crm_qa_pairs.csv` (7 fields, all 160) and `crm_qa_pairs_companion.csv`,
   which now carries the **scoring contract** per pair: `scoring_mode`
   (`scalar_exact` / `table_exact` / `judge_plus_exact`, one per tier per
   Section 9.2), `answer_schema` (column layout + row order, derived from
   the executed result), `numeric_components` (the cells that compare at
   zero tolerance), the `judge_*_version` fields, and `scorer_status`
   (`draft`). `utils/scoring.py` is the reference evaluator for the three
   modes; `tests/test_scoring.py` runs it against all 160 (identity passes,
   a one-step numeric change fails). `author_qa_pairs.py` and `rephrase.py`
   are likewise build-in-memory / validate / then write.

Before any of this, every generator calls
`utils.build_stamp.require_fresh_db()`. `generate_crm.py` records, in both
the DuckDB's `_build_stamp` table and the manifest: a **deterministic
per-table content digest** (every row, fully ordered) plus the DDL/DBML
hashes and CSV hashes, folded into one `build_fingerprint`.
`require_fresh_db()` re-derives the digests from the open connection and
re-hashes the **authoritative** `data/crm_dataset_v2/` bundle (DDL, DBML,
CSVs) as well as the staged mirrors — so a stale DB, a hand-edited table,
or a changed source file all abort the run. `generate_crm.py` itself
builds into `crm_<profile>.duckdb.tmp` and `os.replace()`s it in only
after integrity passes; the previous DB survives a failed rebuild.

Why templates: hand-writing 160 queries drifts — inconsistent aliases,
`BETWEEN` vs half-open ranges, `LIMIT` without a deterministic
`ORDER BY`, and — as the first version proved — a param-name typo that
silently filtered on `''`. A template fixes the query shape once.

### 4b. Seed fixtures — `author_qa_pairs.py` (32, one per family, review only)

A `PAIRS` list of hand-written dicts, one per family, run through the
same execute-and-serialize machinery and written to `fixtures/<profile>/`.
They are **not part of the 160** and are not counted toward any quota —
they exist to give a reviewer a small readable set and to cover shapes a
template can't (CTEs, `CASE` period comparisons). Because they are not
delivered, a fixture question that echoes a templated one is fine.

---

## 5. Open items (all resolved 2026-09-08)

Two tiers were built but held in a separate file pending client rulings
(scope doc Risk R-10: *hold them so a ruling either way costs one merge,
not a rewrite*). Both came back and the held split was removed — all 160
pairs are in `crm_qa_pairs.csv`.

| Item | Ruling | Effect |
|---|---|---|
| **OI-1** — is a single-table tier allowed? (§8.2 vs §9.1) | T1 stays | 32 T1 pairs un-held |
| **OI-4** — empty CSV field → NULL or `""`? | ingests as NULL — the T3 assumption was right | 32 T3 pairs un-held |
| **OI-2** — numeric answer format | deterministic evaluator extracts the first number and compares numerically | golden answers stay plain numbers (no `$` / `,` / unit noun) — no code change |
| **OI-3** — date answers | exact match, no tolerance | already the behaviour |

Recorded in `generator/config.json` under `resolved_open_items`. Still
open: the list format the LLM Judge expects for the 24 `judge_plus_exact`
answers, numeric tolerance = 0 for eval runs (spec change C2), and a
**named** independent human reviewer (Section 15 — the author cannot
self-sign).

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

**Add a question to an existing family:** widen the family's entry in
`generator/catalog.json` (or the data) so `SELECT DISTINCT` returns more
values, or raise its `quota` in `families.json`. No code change.

**Add a family:** write `templates/tN/TN-MM.sql.j2` (string values via
`{{ x | sqlstr }}`), add one object to `generator/families.json`
(`family, tier, template, params, quota, join_path, question, tables,
fields, judge, rationale`), rebalance the tier so the `quota` sum still
equals its §9.1 target, run `gen_family_matrix.py`, and add a seed to
`author_qa_pairs.py`. No Python edit in `scale_pairs.py`.

**Add a tier:** unlikely — T1–T5 are fixed by scope doc §9.2.

**Add a domain (Sales, Finance, PM, Logistics):** `utils/` is entirely
domain-agnostic. Per domain you supply `config.json` (quotas, id prefix,
held rules), `catalog.json` (param → SQL), `families.json`, `labels.json`
(enum → business phrase), the
templates, `schema/`, and the data. The four generator scripts are thin
drivers over those files. Enforce cross-domain question uniqueness (§9.4)
with a normalized-text hash across all domains' CSVs.

---

## 9. What this POC does not do (real Track B adds)

- Cross-corpus duplicate-question hash check across all five domains (§9.4)
- A **named** independent reviewer — CI now runs the full gate (`ruff`,
  `black`, `sqlfluff`, `QA_RELEASE=1 pytest` including the clean-room
  re-execution from `data/crm_dataset_v2/`, and a second-build determinism
  diff — `.github/workflows/qa-pairs-poc.yml`), but §15 still requires a
  person other than the author to sign
  `../taxonomy/crm_taxonomy_review_checklist.md`.
- Ambiguity set for questions with more than one defensible reading
- "Referential" rephrase class (pronoun / named-entity follow-up) — needs
  conversational context, out of scope for single questions
- Git LFS + a `qa-v1.0.0` tag on the delivered set
- The four remaining domains (Sales, Finance, PM, Logistics)
- Python 3.11 on the dev box (3.9.6 here, recorded in every manifest; CI
  runs 3.11). DuckDB is the mandated 0.10.3.
- `scorer_status` moving from `draft` to `approved`/`calibrated` — waits on
  the Platform Owner confirming tolerance=0 and the judge input format.

## 10. Governance documents

| Doc | Purpose |
|---|---|
| `../taxonomy/crm_qa_design_spec.md` | Milestone deliverable — the design, for Platform-Owner sign-off |
| `../taxonomy/crm_taxonomy_review_checklist.md` | Independent-reviewer checklist (author ≠ reviewer) |
| `../taxonomy/open_items_confirmation_request.md` | OI-1..4 write-ups (all resolved 2026-09-08) |
| `../taxonomy/crm_rephrase_plan.csv` | The 16 rephrase groups (generated from `rephrase.py`) |
| `../docs/REMEDIATION.md` | Every review finding and its resolution status |
