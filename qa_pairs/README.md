# CRM Q&A Pair POC

Proof-of-concept for **Track B** of the NLQ Evaluation Framework: going
from "the CRM schema + the frozen golden dataset" to "a set of real,
DuckDB-verified Q&A pairs" in one folder.

The 5 tiers are generated to the full Section 9.1 quota — **160 pairs**
in the delivered **7-field** contract format plus a keyed companion — for
both `dev` and `full`. **32 seed fixtures** (one per family) exist for
review and are *not* part of the 160. **16 rephrase groups** (~10% of the
160) each pair a base release question with 1–2 reworded variants that
share one `result_hash`. See [Status](#status).

This POC uses the **`crm_dataset_v2`** bundle (six tables: `accounts`,
`contacts`, `campaigns`, `contact_campaigns`, `interactions`,
`support_cases` — engagement / campaign / support, no sales pipeline).
`reference_today = 2026-08-01`.

**Docs:** `docs/ARCHITECTURE.md` (how it works — data flow, guarantees,
extension guide) · `docs/REMEDIATION.md` (review findings + what is / is
not fixed) · `taxonomy/crm_qa_design_spec.md` (milestone deliverable) ·
`taxonomy/open_items_confirmation_request.md` (OI-1/OI-2/OI-4).

## Folder layout

```
qa_pairs_poc/
├── README.md
├── data/crm_dataset_v2/          <- the delivered golden dataset (input, read-only)
├── schema/  ddl.sql , er.dbml    <- CRM DDL + ER diagram source (from the bundle)
├── docs/    ARCHITECTURE.md , REMEDIATION.md
├── .sqlfluff                    <- lint config (house style for reference SQL)
├── taxonomy/                     <- the Q&A design + governance
│   ├── crm_qa_design_spec.md , crm_qa_taxonomy_spec.md
│   ├── crm_question_family_matrix.csv     <- GENERATED from families.json
│   ├── crm_allowed_join_paths.csv         <- JP1-JP12
│   ├── crm_answer_format_matrix.csv       <- expected_answer serialization
│   ├── crm_edge_case_coverage_map.csv     <- imperfection -> covering family
│   ├── crm_rephrase_plan.csv              <- GENERATED from rephrase.py GROUPS (16)
│   ├── crm_taxonomy_review_checklist.md
│   └── open_items_confirmation_request.md
├── utils/                        <- domain-agnostic core (nothing CRM-specific)
│   ├── verify.py                <-   execute SQL + serialize + write deterministic log
│   ├── serialization.py         <-   the ONLY place a result becomes an answer string
│   ├── sql.py                   <-   sqlstr() literal quoting + the Jinja env factory
│   ├── sampling.py              <-   even_sample() + N-parameter combos()
│   ├── duckdb_io.py             <-   typed-DuckDB connect + distinct()
│   └── labels.py                <-   generic label lookup (CRM map lives in generator/labels.json)
├── generator/                    <- CRM specifics: data files + thin drivers
│   ├── config.json             <-   domain, id prefix, tier quotas, resolved-OI record
│   ├── catalog.json            <-   param name -> SQL listing its allowed values
│   ├── families.json           <-   32 question families (the source of truth)
│   ├── labels.json             <-   CRM enum token -> business phrase (question text only)
│   ├── base.json               <-   seed, reference_today, imperfection rates (from bundle)
│   ├── generate_crm.py         <-   Step 1: stage bundle -> dataset/ + typed DuckDB + manifest
│   ├── validate_crm.py         <-   Step 2: FK / join-path / imperfection gate
│   ├── scale_pairs.py          <-   Step 3: 160 pairs from config+catalog+families+templates
│   ├── author_qa_pairs.py      <-   Step 3b: 32 seed FIXTURES (review only)
│   ├── rephrase.py             <-   Step 3c: 16 rephrase groups + map (§9.5 companion)
│   ├── gen_family_matrix.py    <-   regenerate the taxonomy matrix CSV from families.json
│   └── templates/t{1..5}/*.sql.j2  <- one SQL template per family ({{ x | sqlstr }})
├── tests/                       <- pytest: contract / templates / ground-truth / scoring / rephrase / sqlfluff
├── dataset/                     <- generated (git-ignored except manifests)
│   └── <profile>/*.csv , crm_<profile>.duckdb , manifest_<profile>.json
├── qa_pairs/<profile>/          <- the RELEASE (generated, tracked)
│   ├── crm_qa_pairs.csv             7-field contract, all 160 (OI-1/OI-4 resolved)
│   ├── crm_qa_pairs_companion.csv   question_id / tier / family / join_path / scoring_mode / schema / hash
│   └── verification_logs/*.json     one per question (160)
└── fixtures/<profile>/          <- 32 seed fixtures (one per family), NOT part of the 160
    ├── crm_seed_fixtures.csv , crm_seed_fixtures_companion.csv
    └── verification_logs/*.json
```

## The flow

```
data/crm_dataset_v2/<profile>/*.csv        (frozen golden dataset - the ONLY data source)
        |
        v   generator/generate_crm.py      stage CSVs; CREATE the DDL in a fresh
        |                                   crm_<profile>.duckdb and COPY the CSVs into
        |                                   TYPED tables; write manifest (hashes, toolchain)
        v
dataset/<profile>/  +  crm_<profile>.duckdb  +  manifest_<profile>.json
        |
        v   generator/validate_crm.py       FK integrity; INNER paths non-empty;
        |                                   LEFT paths have unmatched rows; imperfections present
        v
generator/scale_pairs.py                    for each family:
        |    - explicit per-family quota; even_sample() if quota < available values
        |    - render templates/tN/TN-MM.sql.j2 with StrictUndefined (missing var = error)
        |    - execute against the TYPED crm_<profile>.duckdb  (decimals stay DECIMAL)
        |    - expected_answer via utils/serialization.py ONLY (never hand-typed)
        |    - blocked (error / 0 rows / NULL scalar) -> *.blocked.json, not numbered
        |    - survivors numbered CRM-T{n}-{family}-{seq} AFTER verification
        |    - non-zero exit if any tier misses quota
        v
qa_pairs/<profile>/crm_qa_pairs.csv (+_companion)  +  verification_logs/*.json
        |
        v   pytest tests/                   7-field contract; tier counts == 32/40/32/32/24;
        |                                   normalized-text uniqueness; every template var
        |                                   is provided; re-execute every SQL and match hash
        v
        (downstream) independent reviewer re-runs from qa_pairs/ only; Track C judge gets
        question + expected_answer + judge_reference (NOT reference_sql); Track D exact-match.
```

## Reproduce it

```bash
cd qa_pairs_poc
python3 -m pip install -r requirements.txt          # duckdb==0.10.3, jinja2, pytest, sqlfluff (pinned)

rm -rf dataset qa_pairs fixtures rephrase
for p in dev full; do
  python3 generator/generate_crm.py    --profile $p
  python3 generator/validate_crm.py    --profile $p
  python3 generator/scale_pairs.py     --profile $p
  python3 generator/author_qa_pairs.py --profile $p
  python3 generator/rephrase.py        --profile $p     # after scale_pairs
done
python3 generator/gen_family_matrix.py                  # after editing families.json
python3 -m pytest tests/ -q
```

Each script puts the repo root on `sys.path` itself, so no `PYTHONPATH`
is needed. Requires DuckDB 0.10.3 (the mandated version, §7.1).

`dev` = 1%-scale (fast iteration). **Only `full` is deliverable** (§7.3);
`dev` and `full` answers differ because the datasets hold different data.

Browse in **DBeaver**: point a DuckDB connection at
`dataset/crm_full.duckdb` (close it before re-running the scripts —
DuckDB is single-writer).

## The Q&A pair CSV — 7 fields (contracted, §9.3)

`natural_language_question, expected_answer, reference_sql, reference_tables, reference_fields, judge_reference, derivation_rationale`

`question_id`, `tier`, and all other metadata live in
`crm_qa_pairs_companion.csv` (columns: `question_id, tier, family,
join_path_id, sql_source, scoring_mode, answer_schema, numeric_components,
judge_rubric_version, judge_prompt_version, scorer_status, param_values,
result_hash, natural_language_question`, sorted by `question_id`). Join it
to the contract file on `natural_language_question`.

**`scoring_mode`** (Section 9.2, one per tier) tells the downstream
evaluator how to score each pair — and `utils/scoring.py` is the working
implementation, exercised against all 160 by `tests/test_scoring.py`:

| mode | pairs | scoring |
|---|---:|---|
| `scalar_exact` | 93 | first number in the response, numeric compare at zero tolerance (T1, T3, T2-01/03/04/07/08) |
| `table_exact` | 43 | row- and cell-exact, order significant, numeric cells at zero tolerance (all T4, T2-02/05/06) |
| `judge_plus_exact` | 24 | LLM Judge on the prose **plus** every golden number matched in order at zero tolerance (all T5) |

`answer_schema` / `numeric_components` declare the column layout and which
cells must compare exactly. `scorer_status` is `draft` until the Platform
Owner confirms tolerance=0 and the judge input format.

- `expected_answer` — produced only by `utils/serialization.py`:
  money = 2 decimals always (`Decimal`, not float); counts = plain int;
  percentages = 2 decimals; one row -> `a | b | c`; many rows ->
  `r1a | r1b; r2a | r2b`. See `taxonomy/crm_answer_format_matrix.csv`.
- `reference_sql` — ANSI SQL, runs unmodified in DuckDB, ends with `;`,
  executed against the **typed** DuckDB.
- `judge_reference` — what a good answer contains *in substance*.
  `derivation_rationale` — how the number is computed, every relative
  date grounded against `2026-08-01`.

## How Jinja is used

Each **question family** has one tiny SQL template
(`templates/t2/T2-01.sql.j2`):

```sql
SELECT COUNT(s.case_id) AS case_count
FROM support_cases s INNER JOIN accounts a ON s.account_id = a.account_id
WHERE a.region = {{ region | sqlstr }};
```

The `sqlstr` filter emits the surrounding quotes and doubles any embedded
apostrophe — templates never hand-write `'{{ region }}'`.

`scale_pairs.py` reads the real value list from the CSVs
(`SELECT DISTINCT region FROM accounts`), and each family has an
**explicit quota**. If the quota is smaller than the number of values,
`even_sample()` picks them evenly across the range (endpoints included) —
never the first N. `utils.sampling.combos` builds the cross product for
families with any number of parameters (`T2-07` = channel × title; most
families take one). `StrictUndefined` makes a template that references a
variable its family does not list a hard failure. Every string value is
interpolated through `{{ x | sqlstr }}`, so a value with an apostrophe is
escaped, not injected.

`author_qa_pairs.py` is the non-templated path — a `PAIRS` list of
hand-written seed **fixtures**, one per family (32), through the same
verify-and-serialize machinery. Review aids, not release content.

## Status

Post-review remediation rounds 1 and 2 are done (`docs/REMEDIATION.md`).
32 families → 160 pairs.

| Tier | Quota | Status |
|---|---:|---|
| T1 (7 families) | 32 | Shippable (OI-1 resolved — T1 stays). T1-07 = SLA-met count. |
| T2 (8 families) | 40 | Verified. T2-08 = SLA compliance %. |
| T3 (6 families) | 32 | Shippable (OI-4 resolved — empty field ingests as NULL). |
| T4 (6 families) | 32 | Verified. **All GROUP BY** over a real dimension; every family joins 3 tables that each do work. T4-04 groups by `contacts.title` and exercises the `attribution_weight` NULL imperfection. |
| T5 (5 families) | 24 | Verified. Account engagement always via `accounts→contacts→interactions`. T5-04/05 compare Q1 vs Q2 2026 and do **not** assume a growth direction. |

`rephrase/<profile>/` — **16 groups**, 16 base pairs (all in the 160,
~10%) + 19 reworded variants; every group shares one `result_hash`.

**Still open:**
- No real independent review — a reviewer runs `pytest tests/` + a
  clean-room re-run from a separate checkout and signs the checklist.
- Environment runs Python 3.9.6, not the mandated 3.11 (no 3.11 available
  here). DuckDB **is** the mandated 0.10.3. `library_versions` in every
  manifest and log records both.
- Campaign-window fields (`start_date/end_date/primary_channel`) and
  `is_primary_attribution` / touch timestamps unused — low discriminative
  value on this dataset (windows span the whole timeline), deferred.
- ERD in the bundle still says "Draft v0.2" — Track A must freeze it.
- "Referential" rephrase class (pronoun / follow-up) needs conversational
  context; out of scope for the single-question format, documented.

## Open items — status (2026-09-08)

| # | Question | Resolution |
|---|---|---|
| OI-1 | Does T1 stay a single-table tier? | **Resolved** — T1 stays (Evan Uyehara). Un-held. |
| OI-2 | Exact numeric format for `expected_answer` | **Resolved** — the deterministic evaluator extracts the first number and compares numerically, so golden answers are stored as plain numbers (`28731`, `23184584.00`, `23.21`) — no `$` / thousands separator / unit noun (Aichi Lin). |
| OI-3 | Date-valued answers — exact or tolerant? | **Resolved** — exact match, no tolerance (Evan Uyehara). |
| OI-4 | Empty CSV field → NULL or empty string on ingestion? | **Resolved** — ingests as SQL NULL; the T3 assumption is correct (Aichi Lin). Un-held. |
| — | Judge input format for the 24 `judge_plus_exact` (T5) answers | **Partially open** — serialized as `a \| b; c \| d`, numeric components enforced at zero tolerance by `utils/scoring.py`. Still to confirm the exact list format the Gemini 2.5 Flash Judge expects. Does not block authoring. |
| — | CI-enforced Python 3.11 / ruff / black / independent reviewer | **Partially closed** — `.github/workflows/qa-pairs-poc.yml` runs 3.11 + ruff + black + sqlfluff + `QA_RELEASE=1 pytest` + determinism diff. `pyproject.toml` pins `requires-python`. Dev box is 3.9.6 (in every manifest). A **named** reviewer to sign the checklist is still outstanding (Section 15). |
| — | Numeric tolerance = 0 for our runs | **Open** — eval-config setting; the spec mandates zero tolerance (change C2). |
| — | Independent QA reviewer | **Open** — needs a person named to sign `taxonomy/crm_taxonomy_review_checklist.md`. |
| — | CRM vs Sales question-boundary rule | **Open** — needed before Sales authoring. |
