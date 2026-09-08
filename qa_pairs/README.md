# CRM Q&A Pair POC

Proof-of-concept for **Track B** of the NLQ Evaluation Framework: going
from "the CRM schema + the frozen golden dataset" to "a set of real,
DuckDB-verified Q&A pairs" in one folder. It validates the approach —
schema, dataset staging, per-family Jinja templates, verification
logging — before the Week 3-7 authoring effort scales it to the full
160-pair CRM release.

All five tiers are now scaled to their full Section 9.1 quota — **160
templated pairs** — plus 26 hand-written seed pairs, for both the `dev`
and `full` datasets. See [Status](#status).

This POC uses the **`crm_dataset_v2`** bundle (six tables: `accounts`,
`contacts`, `campaigns`, `contact_campaigns`, `interactions`,
`support_cases` — engagement / campaign / support, no sales pipeline).
`reference_today = 2026-08-01`.

**Docs:** `docs/ARCHITECTURE.md` (how it works — data flow, guarantees,
extension guide) · `taxonomy/crm_qa_design_spec.md` (milestone deliverable
for sign-off) · `taxonomy/crm_qa_taxonomy_spec.md` (tier → schema) ·
`taxonomy/open_items_confirmation_request.md` (OI-1/OI-2/OI-4).

## Folder layout

```
qa_pairs_poc/
├── README.md                     <- you are here
├── data/crm_dataset_v2/          <- the delivered golden dataset (input, read-only)
├── schema/
│   ├── ddl.sql                   <- CRM DDL (from the bundle)
│   └── er.dbml                   <- ER diagram source (dbdiagram.io)
├── docs/
│   └── ARCHITECTURE.md                 <- how it works: data flow, guarantees, extension guide
├── taxonomy/                     <- the Q&A design + governance
│   ├── crm_qa_design_spec.md           <- milestone deliverable, for Platform-Owner sign-off
│   ├── crm_qa_taxonomy_spec.md         <- tier -> schema narrative
│   ├── crm_question_family_matrix.csv  <- 28 families -> 160 pair quota
│   ├── crm_allowed_join_paths.csv      <- JP1-JP12, the only joins a family may use
│   ├── crm_answer_format_matrix.csv    <- expected_answer serialization per shape
│   ├── crm_edge_case_coverage_map.csv  <- imperfection -> covering family
│   ├── crm_rephrase_plan.csv           <- 8 rephrase groups (~10% target, Section 9.5)
│   ├── crm_taxonomy_review_checklist.md <- independent-reviewer sign-off checklist
│   └── open_items_confirmation_request.md <- OI-1 / OI-2 / OI-4 written questions to route
├── generator/
│   ├── base.json                <- seed=42, reference_today=2026-08-01, imperfection rates (from the bundle)
│   ├── generate_crm.py           <- Step 1: stage CSVs -> dataset/ + crm_*.duckdb + manifest
│   ├── validate_crm.py           <- Step 2: FK / join-path / imperfection checks
│   ├── author_qa_pairs.py        <- Step 3a: hand-written seed pairs (1 per family, all tiers)
│   ├── scale_pairs.py            <- Step 3b: Jinja-templated T1-T5 at full quota (160)
│   └── templates/{t1..t5}/*.sql.j2 <- one small SQL template per question family
├── dataset/                      <- output of generate_crm.py (git-ignored)
│   ├── <profile>/*.csv , crm_<profile>.duckdb , manifest_<profile>.json
└── qa_pairs/                     <- output of author_qa_pairs.py / scale_pairs.py
    └── <profile>/
        ├── crm_qa_pairs_seed.csv        <- 15 seed pairs (T2/T4/T5) - clear to ship
        ├── crm_qa_pairs_seed_HELD.csv   <- 11 seed pairs (T1/T3) - held on open items
        ├── crm_t1_pairs_HELD.csv        <- 32 templated T1 - FULL QUOTA, held on OI-1
        ├── crm_t2_pairs.csv             <- 40 templated T2 - FULL QUOTA, clear to ship
        ├── crm_t3_pairs_HELD.csv        <- 32 templated T3 - FULL QUOTA, held on OI-4
        ├── crm_t4_pairs.csv             <- 32 templated T4 - FULL QUOTA, clear to ship
        ├── crm_t5_pairs.csv             <- 24 templated T5 - FULL QUOTA, clear to ship
        └── verification_logs/*.json     <- one JSON log per question (186 total)
```

## The flow

```
data/crm_dataset_v2/<profile>/*.csv        (frozen golden dataset - the ONLY data source)
        |
        v   generator/generate_crm.py
dataset/<profile>/*.csv , crm_<profile>.duckdb , manifest_<profile>.json
        |
        v   generator/validate_crm.py       (FK integrity, INNER paths non-empty,
        |                                     LEFT paths have unmatched rows, imperfections present)
        v
generator/author_qa_pairs.py   (hand: 1 seed pair per family)
   and   generator/scale_pairs.py   (templated: each real dataset value x family template)
        |                            - render SQL from templates/t{1..5}/*.sql.j2
        |                            - execute in DuckDB against dataset/<profile>/
        |                            - expected_answer = query result (NEVER hand-typed)
        |                            - BLOCK on sql error / zero rows / NULL-where-number
        |                            - write a verification log per question
        v
qa_pairs/<profile>/*.csv , qa_pairs/<profile>/verification_logs/*.json
```

Every `expected_answer` is produced by **executing `reference_sql`
against the exported CSVs in DuckDB** (scope doc Section 9.6). A
`verification_logs/<question_id>.json` records the SQL, execution status,
row count, execution time, `result_hash`, and the answer it produced.

## Reproduce it

```bash
cd qa_pairs_poc
python3 -m pip install -r requirements.txt        # duckdb, jinja2

for p in dev full; do
  python3 generator/generate_crm.py   --profile $p
  python3 generator/validate_crm.py   --profile $p
  python3 generator/author_qa_pairs.py --profile $p
  python3 generator/scale_pairs.py    --profile $p
done
```

`dev` runs against the 1%-scale bundle (fast iteration). `full` runs
against delivery scale. **Only `full`-profile answers are deliverable**
(scope doc Section 7.3) — `dev` and `full` answers differ because the
datasets hold different amounts of data.

Browse the data in **DBeaver**: add a DuckDB connection pointed at
`dataset/crm_full.duckdb`. DuckDB is single-process — close DBeaver
before re-running the scripts (the CSVs are never locked).

## The Q&A pair CSV — 9 fields

`question_id,tier,natural_language_question,expected_answer,reference_sql,reference_tables,reference_fields,judge_reference,derivation_rationale`

- `question_id`: `CRM-T{tier}-{family}-{seq}` (templated) or
  `CRM-T{tier}-{family}-SEED-01` (hand-written).
- `expected_answer` serialization (see `taxonomy/crm_answer_format_matrix.csv`):
  1x1 scalar -> `str(value)`; single row -> `a | b | c`; multiple rows
  -> `r1a | r1b; r2a | r2b`.
- `reference_sql`: ANSI SQL, runs unmodified in DuckDB, ends with `;`.
- `judge_reference`: what a good answer contains *in substance* (phrasing
  may vary). `derivation_rationale`: how the number is computed, with
  every relative date grounded against `2026-08-01`.

## How Jinja is used

Hand-writing 160 reference queries drifts (aliases, `BETWEEN` vs
half-open ranges, missing `ORDER BY` before `LIMIT`). Instead each
**question family** has one tiny template in `generator/templates/`:

`templates/t2/T2-01.sql.j2`
```sql
SELECT COUNT(s.case_id) AS result
FROM support_cases s INNER JOIN accounts a ON s.account_id = a.account_id
WHERE a.region = '{{ region }}';
```

`scale_pairs.py` pulls the real value list from the CSVs
(`SELECT DISTINCT region FROM accounts` -> `Central, East, North, South,
West` — never invented), renders the template once per value, executes
it, and writes one verified pair + one verification log each. Family
`T2-07` takes two params (`channel` x `title`) and renders the cross
product. `q_ctx` lets a family reword the question ("active"/"inactive")
without a second template.

`author_qa_pairs.py` is the non-templated path: a `PAIRS` list of
hand-written dicts, one seed per family, run through the same verify +
log machinery. Both paths produce identical CSV and log formats.

## Status

| Tier | Quota | POC output | Status |
|---|---:|---|---|
| T1 | 32 | **32** templated (`crm_t1_pairs_HELD.csv`) + 6 seeds | **Held** pending OI-1 (T1 single-table tier vs shortcut-prevention). Generated, not shipped. |
| T2 | 40 | **40** templated (`crm_t2_pairs.csv`) + 7 seeds | Clear to ship — no open-item dependency. |
| T3 | 32 | **32** templated (`crm_t3_pairs_HELD.csv`) + 6 seeds | **Held** pending OI-4 (empty CSV field -> NULL vs ""). Generated, not shipped. |
| T4 | 32 | **32** templated (`crm_t4_pairs.csv`) + 3 seeds | Clear to ship. |
| T5 | 24 | **24** templated (`crm_t5_pairs.csv`) + 5 seeds | Clear to ship. Deterministic numeric core per pair. |

Total: **160 templated + 26 seed pairs**, on both `dev` and `full`.

**Known gaps (not hidden):**
- `dev` and `full` both produced; the real release needs only `full`.
- No SQLFluff lint on `reference_sql` yet (Section 9.4/9.6).
- No cross-corpus duplicate-question hash check yet.
- No independent review — author and verifier are the same process here
  (Section 9.4 requires them to differ).
- Rephrase-group variants (~10% of the corpus, Section 9.5) not built.
- Each family varies one (or two) filter values; deeper question-shape
  variety within a family is a later authoring pass.

## Open items to route (block scaling)

| # | Question |
|---|---|
| OI-1 | Does T1 stay a single-table tier? (spec §8.2 vs §9.1) |
| OI-2 | Exact numeric string format for `expected_answer` (decimals, thousands separator, currency) |
| OI-4 | Empty CSV field -> NULL or empty string on Pulse ingestion? (breaks every T3 answer if wrong) |
| — | CRM vs Sales question-boundary rule, in writing, before Sales authoring starts |
