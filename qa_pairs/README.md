# Q&A Evaluation Pairs — Track B

**Track B** of the NLQ Evaluation Framework: going from "a domain's frozen
golden dataset" to "a set of real, DuckDB-verified Q&A pairs" — the answer
key the judge and scorecard score the platform against.

**Implemented for all five domains** — CRM, Sales, Finance, Logistics, and
Project Management — sharing one domain-agnostic `utils/` core and one
declarative per-domain pattern (`config.json` + `catalog.json` +
`families.json` + `labels.json` + `templates/t{1..5}/*.sql.j2`), proven
first on CRM and then repeated for the other four. CRM remains the
reference implementation — built first, most heavily reviewed, and the one
with its own taxonomy/design-spec documents in `taxonomy/` — so most of the
domain-specific detail below is written against it. The other four domains
follow the identical shape; see [Other domains](#other-domains).

## Domain status

| Domain | Pairs (160 base, §9.1 quota) | Rephrase groups | Seed fixtures (review only) |
|---|---|---|---|
| CRM | 160 | 16 | 32 (`author_qa_pairs.py`) |
| Sales | 160 | 16 | — |
| Finance | 160 | 16 | — |
| Logistics | 160 | 16 | — |
| Project Management | 160 | 16 | — |

Every domain's tier split is the same (§9.1): T1 32 · T2 40 · T3 32 · T4 32 ·
T5 24. Seed fixtures (`author_qa_pairs.py`, a hand-written review aid, not
release content) remain CRM-only; every other domain's release content is
the 160 templated pairs plus its 16 rephrase groups.

**Contract deviation (scope §9.3), applies to every domain's file:** the
spec freezes `<domain>_qa_pairs.csv` to exactly 7 named fields — *"Do not
add, remove, or rename fields. Internal metadata lives in a separate
companion file."* The delivered file has **9** — `question_id` and `tier`
prepended — because that's easier to browse/join in DBeaver during review.
Both fields still live in the companion too, so regenerating a
spec-compliant 7-field file is a one-line column drop. **Confirm this with
the Platform Owner before treating it as the final delivery format.**

**Docs:** `docs/ARCHITECTURE.md` (how it works — data flow, guarantees,
extension guide, written against CRM but domain-agnostic in its design) ·
`docs/REMEDIATION.md` (dated review-remediation log) ·
`taxonomy/crm_qa_design_spec.md` (CRM's milestone deliverable) ·
`taxonomy/open_items_confirmation_request.md` (CRM's OI-1/OI-2/OI-4).

## Folder layout

```
qa_pairs/
├── README.md
├── schema/  ddl.sql , er.dbml    <- local Q&A schema reference copies
├── docs/    ARCHITECTURE.md , REMEDIATION.md
├── .sqlfluff                    <- lint config (house style for reference SQL)
├── taxonomy/                     <- CRM's Q&A design + governance (not replicated per domain, see note below)
│   ├── crm_qa_design_spec.md , crm_qa_taxonomy_spec.md
│   ├── crm_question_family_matrix.csv     <- GENERATED from families.json
│   ├── crm_allowed_join_paths.csv , crm_answer_format_matrix.csv , crm_edge_case_coverage_map.csv
│   ├── crm_rephrase_plan.csv              <- GENERATED from rephrase.py GROUPS (16)
│   └── crm_taxonomy_review_checklist.md , open_items_confirmation_request.md
├── utils/                        <- domain-agnostic core (nothing domain-specific)
│   ├── verify.py                <-   execute SQL + serialize + write deterministic log
│   ├── serialization.py         <-   the ONLY place a result becomes an answer string
│   ├── sql.py                   <-   sqlstr() literal quoting + the Jinja env factory
│   ├── sampling.py              <-   even_sample() + N-parameter combos()
│   ├── duckdb_io.py             <-   typed-DuckDB connect + distinct()
│   ├── release_bundle.py        <-   the single CLI --version resolves every component's path
│   └── labels.py                <-   generic label lookup (each domain's map lives in its own labels.json)
├── generator/
│   ├── crm/                     <- config.json, catalog.json, families.json, labels.json,
│   │                               generate_crm.py, validate_crm.py, scale_pairs.py, rephrase.py,
│   │                               author_qa_pairs.py (seed fixtures, CRM-only), gen_family_matrix.py,
│   │                               templates/t{1..5}/*.sql.j2
│   ├── sales/, finance/, logistics/, project_management/
│   │                            <- same four-driver shape as crm/ (generate_<domain>.py,
│   │                               validate_<domain>.py, scale_pairs_<domain>.py, rephrase.py),
│   │                               no seed-fixture script; own config/catalog/families/labels/templates
│   └── base.json                <- shared declarative defaults
├── tests/                       <- pytest: contract / templates / ground-truth / scoring / rephrase / sqlfluff
├── dataset/                     <- generated (git-ignored except manifests)
└── fixtures/<profile>/          <- CRM's 32 seed fixtures, NOT part of the 160
```

## The flow

```
tmp/generated/<domain>/dev/imperfect/*.csv        (dev source)
release/<version>/<domain>/dataset/*.csv          (full source)
        |
        v   generator/<domain>/generate_<domain>.py   resolve source from config; stage CSVs;
        |                                   CREATE the canonical DDL in a fresh
        |                                   <domain>_<profile>.duckdb and COPY the CSVs into
        |                                   TYPED tables; write manifest (hashes, toolchain)
        v
dataset/<profile>/  +  <domain>_<profile>.duckdb  +  manifest_<profile>.json
        |
        v   generator/<domain>/validate_<domain>.py    FK integrity; INNER paths non-empty;
        |                                   LEFT paths have unmatched rows; imperfections present
        v
generator/<domain>/scale_pairs[_<domain>].py            for each family:
        |    - explicit per-family quota; even_sample() if quota < available values
        |    - render templates/tN/TN-MM.sql.j2 with StrictUndefined (missing var = error)
        |    - execute against the TYPED <domain>_<profile>.duckdb  (decimals stay DECIMAL)
        |    - expected_answer via utils/serialization.py ONLY (never hand-typed)
        |    - blocked (error / 0 rows / NULL scalar) -> *.blocked.json, not numbered
        |    - survivors numbered <PREFIX>-T{n}-{family}-{seq} AFTER verification
        |    - non-zero exit if any tier misses quota
        v
generator/<domain>/rephrase.py                          16 rephrase groups + map (§9.5 companion)
        v
dev:  tmp/generated/<domain>/dev/qa_pairs/
full: release/<version>/<domain>/qa_pairs/
       <domain>_qa_pairs.csv (+_companion) + verification_logs/*.json
       rephrase/*.csv + rephrase/verification_logs/*.json
        |
        v   pytest tests/                   9-field contract; tier counts == 32/40/32/32/24;
        |                                   normalized-text uniqueness; every template var
        |                                   is provided; re-execute every SQL and match hash
        v
        (downstream) independent reviewer re-runs from the released bundle only; Track C judge gets
        question + expected_answer + judge_reference (NOT reference_sql); Track D exact-match.
```

The single CLI `--version` resolves every component's path (dataset, Q&A
pairs, judge evidence, scorecard) to one `release/<version>/<domain>/`
bundle — see `utils/release_bundle.py` and the root `README.md`'s
End-to-End Pipeline section. The retired `release/<domain>/<version>/`
domain-first layout is read-only compatibility, never written to.

## Reproduce it

The preferred entry point is the root CLI (see `README.md` / `DEVELOPMENT.md`):

```bash
python main.py build-dataset --domain crm --profile full
python main.py qa-build      --domain crm --profile full   # stage, validate, generate, rephrase
```

Replace `crm` with `sales`, `finance`, `logistics`, or `project_management`
for any other domain — the command surface is identical.

The original per-script path remains available for backward compatibility
and local troubleshooting (each script puts the repo root on `sys.path`
itself, so no `PYTHONPATH` is needed):

```bash
cd qa_pairs
for p in dev full; do
  python3 generator/crm/generate_crm.py    --profile $p
  python3 generator/crm/validate_crm.py    --profile $p
  python3 generator/crm/scale_pairs.py     --profile $p
  python3 generator/crm/rephrase.py        --profile $p     # after scale_pairs
done
python3 generator/crm/author_qa_pairs.py --profile dev        # CRM-only seed fixtures
python3 generator/crm/gen_family_matrix.py                     # after editing families.json
python3 -m pytest tests/ -q
```

Requires DuckDB 0.10.3 (the mandated version, §7.1). `dev` = 1%-scale (fast
iteration); **only `full` is deliverable** (§7.3) — `dev` and `full`
answers differ because the datasets hold different data.

Browse in **DBeaver**: point a DuckDB connection at
`dataset/<domain>_full.duckdb` (close it before re-running the scripts —
DuckDB is single-writer).

## The Q&A pair CSV — 9 fields (§9.3 contract is 7 — see deviation above)

`question_id, tier, natural_language_question, expected_answer, reference_sql, reference_tables, reference_fields, judge_reference, derivation_rationale`

Also carried in `<domain>_qa_pairs_companion.csv` — which is still where
every other piece of metadata lives (columns: `question_id, tier, family,
join_path_id, sql_source, scoring_mode, answer_schema, numeric_components,
judge_rubric_version, judge_prompt_version, scorer_status, param_values,
result_hash, rephrase_group_id, natural_language_question`, sorted by
`question_id`). Join on `question_id` (or `natural_language_question`) —
`test_contract.py` asserts the two files never disagree on either.

**`scoring_mode`** (Section 9.2, one per tier) tells the downstream
evaluator how to score each pair, and `utils/scoring.py` is the working
implementation:

| mode | scoring |
|---|---|
| `scalar_exact` | first number in the response, numeric compare at zero tolerance (T1, most T2/T3) |
| `table_exact` | row- and cell-exact, order significant, numeric cells at zero tolerance (T4, some T2) |
| `judge_plus_exact` | LLM Judge on the prose **plus** every golden number matched in order at zero tolerance (T5) |

`answer_schema` / `numeric_components` declare the column layout and which
cells must compare exactly.

- `expected_answer` — produced only by `utils/serialization.py`:
  money = 2 decimals always (`Decimal`, not float); counts = plain int;
  percentages = 2 decimals; one row -> `a | b | c`; many rows ->
  `r1a | r1b; r2a | r2b`.
- `reference_sql` — ANSI SQL, runs unmodified in DuckDB, ends with `;`,
  executed against the **typed** DuckDB.
- `judge_reference` — what a good answer contains *in substance*.
  `derivation_rationale` — how the number is computed, every relative
  date grounded against the domain's fixed `reference_today`.

## How Jinja is used

Each **question family** has one tiny SQL template
(`templates/t2/T2-01.sql.j2`, example from CRM):

```sql
SELECT COUNT(s.case_id) AS case_count
FROM support_cases s INNER JOIN accounts a ON s.account_id = a.account_id
WHERE a.region = {{ region | sqlstr }};
```

The `sqlstr` filter emits the surrounding quotes and doubles any embedded
apostrophe — templates never hand-write `'{{ region }}'`.

`scale_pairs[_<domain>].py` reads the real value list from the CSVs
(`SELECT DISTINCT region FROM accounts`), and each family has an
**explicit quota**. If the quota is smaller than the number of values,
`even_sample()` picks them evenly across the range (endpoints included) —
never the first N. `utils.sampling.combos` builds the cross product for
families with more than one parameter. `StrictUndefined` makes a template
that references a variable its family does not list a hard failure. Every
string value is interpolated through `{{ x | sqlstr }}`, so a value with an
apostrophe is escaped, not injected.

Adding a family to any domain is one `families.json` entry plus one
`.sql.j2` template — no code change (see "Extending it" in
`docs/ARCHITECTURE.md`).

## CRM — reference implementation detail

Post-review remediation rounds 1 and 2 are done (`docs/REMEDIATION.md`).
32 families → 160 pairs.

| Tier | Quota | Status |
|---|---:|---|
| T1 (7 families) | 32 | Shippable (OI-1 resolved — T1 stays). T1-07 = SLA-met count. |
| T2 (8 families) | 40 | Verified. T2-08 = SLA compliance %. |
| T3 (6 families) | 32 | Shippable (OI-4 resolved — empty field ingests as NULL). |
| T4 (6 families) | 32 | Verified. **All GROUP BY** over a real dimension; every family joins 3 tables that each do work. T4-04 groups by `contacts.title` and exercises the `attribution_weight` NULL imperfection. |
| T5 (5 families) | 24 | Verified. Account engagement always via `accounts→contacts→interactions`. T5-04/05 compare Q1 vs Q2 2026 and do **not** assume a growth direction. |

**Open items — status (2026-09-08):**

| # | Question | Resolution |
|---|---|---|
| OI-1 | Does T1 stay a single-table tier? | **Resolved** — T1 stays (Evan Uyehara). |
| OI-2 | Exact numeric format for `expected_answer` | **Resolved** — the deterministic evaluator extracts the first number and compares numerically, so golden answers are stored as plain numbers (`28731`, `23184584.00`, `23.21`) — no `$` / thousands separator / unit noun (Aichi Lin). |
| OI-3 | Date-valued answers — exact or tolerant? | **Resolved** — exact match, no tolerance (Evan Uyehara). |
| OI-4 | Empty CSV field → NULL or empty string on ingestion? | **Resolved** — ingests as SQL NULL; the T3 assumption is correct (Aichi Lin). |

Remaining historical open items (CRM-vs-Sales question-boundary rule, the
judge input list format, independent reviewer sign-off) are tracked in
`taxonomy/open_items_confirmation_request.md` and
`taxonomy/crm_taxonomy_review_checklist.md`, not reproduced here.

## Other domains

Sales, Finance, Logistics, and Project Management were each built after
CRM, as an independent pipeline sharing the same domain-agnostic `utils/`
and the identical four-driver shape (`generate_<domain>.py` →
`validate_<domain>.py` → `scale_pairs_<domain>.py` → `rephrase.py`), namespaced
so none of their files can collide with CRM's or each other's. Each has its
own `config.json`, `catalog.json`, `families.json`, `labels.json`, and
`templates/t{1..5}/*.sql.j2` tuned to that domain's entities and business
language — see each domain's `generators/<domain>/README.md` (and
`generators/logistics/README.md`, currently missing — tracked in
`DOCUMENTATION_IMPLEMENTATION_PLAN.md`) for the dataset side, and
`config/generation/<domain>/README.md` for configuration.

None of the four has its own `taxonomy/`-style design-spec document set or
CRM's granular per-step planning docs — by design (see the Documentation
Implementation Plan's explicit non-recommendation against replicating
those), not because the work is incomplete. Each domain's own
`generators/<domain>/README.md` and this file's shared sections above are
the living reference; CRM's `taxonomy/` documents remain CRM-specific
historical/governance artifacts, not a template every domain must duplicate.
