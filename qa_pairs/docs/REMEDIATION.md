# Remediation

## Review 5 (2026-09-10, second release-readiness review)

| Finding | Fix |
|---|---|
| **[P1] The `scorer` label did not implement the scope's scoring contract.** Everything non-scalar was tagged `judge`, dropping the deterministic exact-match that Section 9.2 requires for T2/T4 and the "exact match on every numeric component" that T5 requires on top of the judge. Tests only asserted a routing split; no evaluator ran. | Replaced the two-value `scorer` with the three scope modes. **`utils/scoring.py`** is a working evaluator: `scalar_exact` (first-number extraction + zero-tolerance numeric compare), `table_exact` (row- and cell-exact, order significant, numeric cells at zero tolerance), `judge_plus_exact` (every golden numeric component must appear in order at zero tolerance **and** the judge must not reject the prose). `families.json` declares `scoring_mode` + `answer_shape` per family. The companion gains `scoring_mode, answer_schema, numeric_components, judge_rubric_version, judge_prompt_version, scorer_status` (all `scorer_status=draft` until the Platform Owner confirms tolerance=0 and the judge input format). Mapping: T1/T3 + 5 T2 families → `scalar_exact` (93 pairs); T2-02/05/06 + all T4 → `table_exact` (43); all T5 → `judge_plus_exact` (24). **`tests/test_scoring.py`** runs the evaluator against the real 160: every answer scores itself as a pass, and bumping the first number by the smallest step makes >150 of them fail. `test_contract.py` also checks each mode is legal for its tier. |
| **[P1] The freshness guard could be bypassed by editing DuckDB tables.** It compared a `_build_stamp` row and the staged CSVs, not the live table contents or the authoritative bundle. A hand-edited `accounts` row passed. | `generate_crm.py` now computes a **deterministic per-table content digest** (every row, fully ordered) and stores it in `_build_stamp` and the manifest; `build_fingerprint` covers those digests plus the DDL **and DBML** hashes. `require_fresh_db()` re-derives the digests from the open connection and re-hashes `data/crm_dataset_v2/` — the **authoritative** DDL, DBML, and CSVs, plus the staged mirrors. Verified: `UPDATE accounts SET region='TAMPERED' …` on a copied DuckDB now aborts every downstream step with "DuckDB table contents changed since build: ['accounts']". |
| **[P2] Generators were still destructive before validation.** `rephrase.py` / `author_qa_pairs.py` deleted the previous output before the freshness check and before hash/missing-base validation; `generate_crm.py` deleted the last good DuckDB before the new one passed integrity. | All four now build in memory / into a temp path, run every check, and only then delete + write. `generate_crm.py` builds `crm_<profile>.duckdb.tmp`, validates, and `os.replace()`s it in atomically; a failure unlinks the temp file and leaves the previous release intact. |
| **[P2] The ground-truth test reused the artifact it audits.** It opened the committed `dataset/crm_full.duckdb`. | New `authoritative_con` fixture builds a fresh in-memory DuckDB from `data/crm_dataset_v2/crm_ddl.sql` + the full-profile source CSVs; `test_ground_truth.py` re-executes all 160 `reference_sql` against **that** and checks answers + hashes. `conftest.py` honours `QA_RELEASE=1`: missing release artifacts become a hard error instead of a skip. |
| **[P2] No CI / lint / format / Python-version enforcement.** | Added `.github/workflows/qa-pairs-poc.yml` (Python 3.11): `ruff check`, `black --check`, `sqlfluff lint schema/ddl.sql`, full pipeline build, `QA_RELEASE=1 pytest`, and a second-build byte-identical determinism diff. `pyproject.toml` pins `requires-python = ">=3.11"` and the ruff/black config; `requirements-dev.txt` pins `ruff` and `black`. The whole tree is now ruff- and black-clean. |

Still open (documented, external): numeric tolerance = 0 must be set in the
Platform Owner's evaluator config (spec C2); the judge input/list format for
the 24 `judge_plus_exact` (T5) answers; a **named** independent reviewer to sign
`crm_taxonomy_review_checklist.md` (Section 15 - the author cannot self-sign);
a Slack export/permalink for the OI rulings; Python 3.11 on the dev box
(3.9.6 today, recorded in every manifest; CI runs 3.11).

Verified after this pass: full clean regen (dev + full), `ruff check` +
`black --check` clean, `QA_RELEASE=1 pytest` → **54 passed**, double-run
`diff -r qa_pairs/` + `manifest_full.json` byte-identical, clean-room
re-execution of all 160 from the authoritative bundle with 0 drift,
tamper test rejected. `crm_qa_pairs.csv` unchanged.

## Review 4 (2026-09-09, release-readiness review)

| Finding | Fix |
|---|---|
| **[P1] DDL fails its declared SQLFluff gate via the CLI.** `python3 -m sqlfluff lint schema/ddl.sql` exited 1 with ~83 LT02 (indentation) errors: `.sqlfluff` set `tab_space_size = 2` but the authoritative DDL (byte-identical to the Track-A bundle) is 4-space indented. The 40-test suite only linted `reference_sql`, never the DDL. | `.sqlfluff` set to `tab_space_size = 4` (matches the bundle DDL; `reference_sql` is stored collapsed to one line so indentation rules never applied to it). New `tests/test_sqlfluff.py::test_authoritative_ddl_is_sqlfluff_clean` lints `schema/ddl.sql` against the project `.sqlfluff` (layout rules **on**, only AL01/RF04 house-style off) exactly as the CLI would. `sqlfluff` moved out of "optional" in `requirements.txt`; `pytest.importorskip` replaced with a hard `import`. |
| **[P1] 67 released answers had no confirmed scoring path.** All 32 T4, all 24 T5, and 11 T2 (T2-02/05/06) are multi-value; the "all 160 deterministically shippable" claim was wrong. | `families.json` gains a `scorer` field (`deterministic` \| `judge`); `crm_qa_pairs_companion.csv` gains a `scorer` column. 93 pairs are `deterministic` (single plain number), 67 are `judge`. `test_contract.py` fails the build if anything tagged `deterministic` carries a `\|` / `;` / non-numeric answer, and asserts the 93/67 split. Docs no longer claim the 67 are deterministically scorable; the open item is the Judge's golden-list format (tracked in `open_items_confirmation_request.md`). |
| **[P1] QA generation trusted a potentially stale DuckDB.** `scale_pairs.py` / `author_qa_pairs.py` / `rephrase.py` read only `dataset_version` from the manifest before opening the existing `.duckdb`. | `generate_crm.py` computes a `build_fingerprint` (hash of `dataset_version` + DDL sha + every CSV sha/rowcount), stamps it into a `_build_stamp` table in the DuckDB, and records it in the manifest. New `utils/build_stamp.require_fresh_db()` — called right after every `connect_typed()` — aborts unless the in-DB fingerprint matches the manifest **and** the on-disk CSV + DDL hashes still match the manifest. Verified: appending a byte to a staged CSV now aborts the run with "staged CSV changed since manifest". |
| **[P2] Integrity failures were non-fatal.** `generate_crm._integrity()` returned `"FAILED (...)"` strings / `False` but the manifest and DuckDB were still written. | `_assert_integrity()` raises `SystemExit` on any failed check (fk / row-cap / inner-verified / left-unmatched) **before** the manifest or build stamp is written, and unlinks the partial `.duckdb` so no later step can trust it. |
| **[P2] `crm_qa_design_spec.md` §4/§5/§7 stale.** Still said 1–2-param sampling, OI-2 pending, 8 rephrase groups / 4 classes / "variants not yet authored". | §4 rewritten to N-param cartesian + `even_sample`; §5 rewritten to the OI-2 ruling + the `scorer` split table; §7 rewritten to 16 groups / 19 variants / 3 classes / domain-prefixed IDs / Referential-omitted rationale. |
| **`scale_pairs.py` deleted the previous release before validating quota.** `shutil.rmtree(out)` ran first; a quota failure at the end left partial CSVs and a half-updated log dir. | The release is now built entirely in memory (pairs, companion, log payloads); the quota check runs first; only on success does it `rmtree` + write logs + write CSVs. Message on failure: "QUOTA FAILURE (nothing written)". |
| **Rephrase variant IDs not domain-prefixed.** `RG-01-V01` instead of `CRM-RG-01-V01`. | `rephrase.py` prefixes every `pair_group_id` and variant `question_id` with `config.json` `id_prefix`. `crm_rephrase_map.csv`, `crm_rephrase_plan.csv`, and the per-variant logs are all `CRM-RG-*`. |
| **`serialization.py` dropped the time component of a non-midnight timestamp.** `datetime` → `.date().isoformat()` unconditionally. | Non-midnight `datetime` now serializes as `YYYY-MM-DDTHH:MM:SS`; exact-midnight stays `YYYY-MM-DD`. |
| **`crm_taxonomy_review_checklist.md` still listed "8 groups; variants not yet generated".** | Line updated to 16 groups / 19 variants / 3 classes, marked done. |
| **README / ARCHITECTURE Jinja example showed `'{{ region }}'`.** | Both now show `{{ region \| sqlstr }}` with a note that templates never hand-write the quotes. |

Verified after this pass: full clean regen (dev + full), `pytest tests/ -q`
→ **44 passed**, double-run `diff -r qa_pairs/` byte-identical, all 160
`reference_sql` re-executed with 0 drift against `result_hash`.

**Still open:** numeric tolerance = 0 for eval runs (spec change C2, client
config); the golden-list format the LLM Judge expects for the 67
judge-scored answers; a named independent human reviewer; Python 3.11
(unavailable — 3.9.6 recorded in every manifest); Track-A ERD freeze.

## Open items resolved (2026-09-08) — held split removed

The Platform Owner team answered OI-1..4 in Slack:

| Item | Ruling | Change |
|---|---|---|
| **OI-1** T1 single-table tier | T1 stays (Evan Uyehara) | 32 T1 pairs un-held |
| **OI-4** empty CSV field on ingestion | ingests as SQL NULL (Aichi Lin) | 32 T3 pairs un-held |
| **OI-2** numeric answer format | eval tool's Deterministic evaluator extracts the first number and compares numerically; LLM Judge (Gemini 2.5 Flash) for semantic (Aichi Lin) | golden answers stay plain numbers — `utils/serialization.py` unchanged; a test asserts no `$`/`,`/`%` in scalar answers |
| **OI-3** date answers | exact match, no tolerance (Evan Uyehara) | already the behaviour |

The `held` mechanism (config `held` rules, `crm_qa_pairs_HELD.csv`,
`crm_seed_fixtures_HELD.csv`, the `held` companion column, the per-`PAIRS`
`held=` flags) was **removed** — all 160 pairs are in `crm_qa_pairs.csv`.
`generator/config.json` records the rulings under `resolved_open_items`.
Determinism, 40 tests, and 160-pair re-verification re-confirmed after
the change.

Still open: (a) numeric tolerance = 0 for eval runs (config, spec change
C2); (b) how multi-value answers (T4 grouped, T2/T5 lists) are scored —
default LLM Judge, awaiting client steer; (c) an independent human
reviewer to sign the checklist.

## Review 3 (2026-09-08, third pass — independent release review)

| Finding | Fix |
|---|---|
| **Non-deterministic verification logs.** `verify.write_log` wrote `execution_time_seconds` (wall-clock derived) into every `qa_pairs/<profile>/verification_logs/*.json`. Those files are tracked release artefacts, so a clean-room re-run was **not** byte-identical (HC-7 / §7.2) — a `diff -r` of two pipeline runs showed 121 log files differing on that one line. | `execution_time_seconds` removed from the log and `seconds`/`time` removed from `verify.py`. The log docstring already claimed "no wall-clock … a clean-room re-run must produce byte-identical logs"; it now holds. Two full pipeline runs now `diff -r` clean across `qa_pairs/` and both manifests. |
| **`utils/` not actually domain-agnostic.** `utils/verify.py` hard-coded `"domain": "crm"` in every log; `utils/labels.py` *was* the entire CRM enum→phrase map (60 entries) living in the "domain-agnostic core". | `write_log()` takes a `domain` argument (drivers pass it from `config.json` / the manifest). The CRM label map moved to `generator/labels.json`; `utils/labels.py` is now a generic `load(path)` + `label(value, mapping)`. `grep -riE 'crm|<table names>' utils/` is now clean except doc comments. |
| **`crm_rephrase_plan.csv` claimed "generated", wasn't.** REMEDIATION and README said it is generated from `rephrase.py`'s `GROUPS`; no code wrote it. | `rephrase.py` now regenerates `taxonomy/crm_rephrase_plan.csv` from `GROUPS` on every run (profile-independent, deterministic). |
| **Edge-case coverage map overclaimed.** The `attribution_weight` row still described T4-04 as "grouped by member_status" (round 2 changed it to `contacts.title`). The boundary-date row cited T1-03 / T1-07 as covering families without noting they are **held** (OI-1), and T2-08 / T5-02 boundary sensitivity is immaterial (3–4 boundary rows in 144k). | `taxonomy/crm_edge_case_coverage_map.csv` rows rewritten to match the shipped SQL and to state honestly that decisive boundary-date coverage is in the held T1 families; shipped boundary coverage is structural (correct logic), not numerically material. Flagged as open below. |
| **Stale doc counts.** `ARCHITECTURE.md` said "seed fixtures (26, review only)" — there are 32 (one per family). | Corrected. |

Independent re-verification this pass: a fresh in-memory DuckDB built directly
from `schema/ddl.sql` + `data/crm_dataset_v2/full/*.csv`, all 160 `reference_sql`
re-executed — every `expected_answer` and every companion `result_hash` matched
(0 drift). 19 rephrase variants re-executed — 0 drift. 40 pytest tests pass.
Double-run `diff -r qa_pairs/full` is now clean.

**Still open after this pass:**
- Boundary-date imperfection (§9.4) is only *decisively* exercised by T1-03 /
  T1-07, both **held** pending OI-1. If OI-1 keeps T1 as a control tier, these
  ship and the gap closes; if not, a T2/T4/T5 family that turns on a boundary
  timestamp should be added. The dataset only contains 3–4 boundary rows, so a
  shipped question with a *visible* boundary effect would be contrived.
- `member_status` is touched only by a review-only seed fixture, not a shipped
  family (T4-04 groups by `contacts.title`). Not a spec requirement; noted.
- Independent review is still same-tooling: this pass re-executed ground truth
  from the authoritative DDL+CSVs (not the staged copies), but the reviewer and
  the author share a codebase.

## Review 2 (2026-09-08, second pass)

| Blocker | Fix |
|---|---|
| **T5-05 said "grew fastest" but every answer was a decline.** | T5-04/05 questions no longer assume a direction ("how did engagement change" / "which region had the best trend"). `judge_reference` states the result is the highest pct_change — a gain, or the smallest decline. `test_templates.py::test_no_question_template_assumes_growth_direction`. |
| **T4-04's third table did no work.** | T4-04 now `GROUP BY contacts.title` — contacts supplies the dimension, contact_campaigns the measure, campaigns the filter. All 6 T4 families are checked to join 3 tables with 2 INNER JOINs and a `GROUP BY` (`test_templates.py`). |
| **Rephrase: no "last quarter", mislabelled classes, plan ≠ code, kept out of the 160.** | 16 groups (~10% of 160); each **base is a real 160 pair** (linked by `question_id` in `crm_rephrase_map.csv`). Temporal groups now include a "previous quarter" / "last quarter" variant. Classes are Temporal / Lexical / Rephrase — "Referential" needs conversation context and is documented as out of scope, not faked. `crm_rephrase_plan.csv` is **generated** from `rephrase.py`'s `GROUPS`. |
| **Wrong runtime (Python 3.9, DuckDB 1.4.5).** | `duckdb==0.10.3` — the mandated version — installs and runs on this Python; pinned and re-verified against it. Python 3.11 is unavailable in this environment; 3.9.6 is recorded in every manifest and log's `library_versions`, flagged in README. |
| **Manifest / log gaps.** | Manifest adds `generated_at` (fixed per dataset version), `library_versions`, `row_cap_per_table`, and an `integrity` block (fk / row-cap / inner-verified / left-unmatched). Logs: `qa_version` is `0.3.0` (not `-dev`), `profile` added, the misleading `verification_timestamp` removed and replaced with `deterministic: true`. `result_hash` is now `SHA-256(canonical serialized answer)`, not `repr(rows)`. |
| **Docs out of sync (fixture count, T4-04 params, companion order, "not implemented").** | 32 fixtures (one per family). Companion is sorted by `question_id`; join to a contract file on `natural_language_question`. `crm_qa_taxonomy_spec.md`, `ARCHITECTURE.md` §8/§9, `crm_answer_format_matrix.csv` rewritten. `crm_question_family_matrix.csv` and `crm_rephrase_plan.csv` are generated. |
| **CRM-specific, unsafe SQL interpolation, 1-2 param limit.** | `utils/` is a domain-agnostic package (`verify`, `serialization`, `sql`, `sampling`, `duckdb_io`, `labels`). CRM specifics are three data files: `config.json` (quotas/prefix/held), `catalog.json` (param → SQL), `families.json`. Every string value goes through `{{ x \| sqlstr }}` (doubles quotes — `test_templates.py::test_sqlstr_escapes_apostrophe` + a test that no template bare-interpolates). `combos()` handles any number of parameters. |
| **SQLFluff not a gate.** | `tests/test_sqlfluff.py` lints every distinct `reference_sql` via the SQLFluff Python API (fast); `.sqlfluff` documents the house style (implicit aliasing, keyword column names). Hard gate — no skip. |

Verified after this pass: clean-room `generate_crm` + 160 SQL re-execution,
40 tests pass, dev + full regenerated.

---

# Remediation — response to the 2026-09-08 review (round 1)

An independent review of the first POC (branch `DM/qa_pair_7sept`,
commit `579abf1`) found several correctness failures, weak taxonomy
coverage, and docs that overclaimed readiness. The findings were
reproduced and are tracked here.

**Done:** Phase 0 (verifier + delivery contract), Phase 1 (T5
correctness), and Phase 2 (taxonomy redesign — SLA, attribution, T4
GROUP BY, rephrase pairs). The generator was also refactored to be
data-driven (`families.json`), applying the ponytail "minimum viable"
principle — `scale_pairs.py` went 506 → 180 lines and adding a family is
now one JSON entry + one `.sql.j2` file. The npm ponytail plugin itself
was not installed; run `npx ponytail@latest` if you want its slash
commands.

Still open: F (independent review — a process step) and G (Track-A ERD
freeze — not Track B's to do).

## Fixed

| # | Finding | Fix |
|---|---|---|
| 1 | **Delivery format: 9 fields, contract wants 7.** `question_id` / `tier` were in the pair CSV. | `scale_pairs.py` now emits `crm_qa_pairs.csv` with exactly the 7 contract fields + `crm_qa_pairs_companion.csv` keyed by `question_id`. `tests/test_contract.py` asserts the field list. |
| 2 | **T1-02 returned 0 for all 5 campaign-status questions.** Spec passed `campaign_status`, template read `{{ status }}`; Jinja rendered the undefined var as `''` → `WHERE status = ''`. | Template now reads `{{ campaign_status }}`. Jinja env uses `StrictUndefined`, and `validate_template_vars()` fails the run if any template references a variable its spec does not provide. `tests/test_templates.py` covers it. Active count is now 1235 (was 0). |
| 3 | **Typed re-execution changed answers.** The verifier used `read_csv_auto`, loading `DECIMAL` columns as `float` → `23184584.0` vs the DDL's `23184584.00`. | Both generators now query the **typed** `crm_<profile>.duckdb` that `generate_crm.py` builds by running the DDL and `COPY`-ing the CSVs. `serialization.py` quantizes money to 2 places from `Decimal`. |
| 4 | **T5 used the wrong account-engagement path.** T5-03/04/05 joined `interactions.account_id = accounts.account_id`, dropping 27,717 interactions whose direct `account_id` is NULL but whose contact belongs to the account. For Central, T5-04 read 17,742 / 2,872 instead of 20,923 / 3,364. T5-05 even picked a different winning region. | T5-03/04/05 templates and the T5 seed fixtures now route account engagement through `accounts → contacts → interactions`, consistent with T5-01 and the T4 families. |
| 5 | **T5 quarter comparison was a stub.** Data ends 2026-07-28; "this quarter" (Q3) had 28 days vs a full Q2, so every result was ≈ −83% and labelled "growth". | T5-04/05 now compare **Q1 2026 vs Q2 2026** — the two most recent *complete* calendar quarters relative to `reference_today`. Questions and rationales say so explicitly. |
| 6 | **T5-04 answer had only two totals** for "how did engagement change". | Now returns `prev_quarter_points \| this_quarter_points \| absolute_change \| pct_change \| direction`. |
| 7 | **Family allocation biased by file order** — `scale()` filled families sequentially and cut off the last one (T5-05 got 1 tier of 3, T2-07 got 3 of 35 combos). | Each family has an **explicit `quota`**; `even_sample()` picks values evenly across the range when quota < available; `main()` asserts the per-family quotas sum to the Section 9.1 tier quota. |
| 8 | **Corpus was 186 rows, not a clean 160**, with 13 seed questions duplicating templated ones. | Seeds moved to `fixtures/` — review-only, never counted or delivered. Release is exactly 160 (`test_contract.py::test_total_is_exactly_160`). Cross-file normalized-text uniqueness is tested. |
| 9 | **Raw enum values in questions** (`FormSubmission`, `GeneralInquiry`, "engagement-point"). | `labels.py` maps every enum to a business label; applied to question text only (SQL / fields / rationale keep the real tokens). `test_contract.py` bans a list of raw tokens in questions. |
| 10 | **Verifier log-id reuse.** `seq` incremented only on success, so a blocked candidate's `{id}.json` could be overwritten. | Pairs are verified first, then numbered `CRM-T{n}-{family}-{seq}` in a second pass; blocked candidates get a distinct `{family}__{params}.blocked.json`. |
| 11 | **No fail-on-under-quota.** | `scale_pairs.py` exits non-zero if any tier misses its quota. |
| 12 | **Unpinned deps; `dataset_version` mismatch.** `requirements.txt` used `>=`; the manifest wrote `crm_dataset_v2-full` while the bundle config says `dataset-v1.0.0`. | Deps pinned to the verified versions (`duckdb==1.4.5`, `jinja2==3.1.6`, `pytest==8.4.2`). Manifest keeps the canonical `dataset-v1.0.0` tag, adds `profile`, `schema_ddl_sha256`, and a `toolchain` block. The scope doc's DuckDB 0.10.3 target is documented as unbuildable on the current Python. |
| 13 | **No automated tests.** | `tests/`: `test_contract.py` (7-field, tier counts, uniqueness, held split, no raw tokens, SQL ends `;`), `test_templates.py` (every template var provided, serialization units, `even_sample`), `test_ground_truth.py` (re-execute every SQL from the released CSV, match `result_hash`), `test_sqlfluff.py` (skipped unless `sqlfluff` installed). |

## Phase 2 — taxonomy redesign (done)

| # | Finding | Fix |
|---|---|---|
| A | **Zero SLA / resolution coverage.** `sla_due_at / resolved_at` unused. | `T1-07` (SLA-met count by priority, single-table), `T2-08` (SLA compliance % by industry), `T4-03` (SLA % grouped by category, per customer tier). Within-SLA = `resolved_at IS NOT NULL AND resolved_at <= sla_due_at`. |
| B | **Attribution-weight NULL imperfection unexercised** (§9.4). | `T4-04`: `SUM(attribution_weight)` grouped by `member_status` for a campaign type. NULL rows are skipped by `SUM` — the declared NULL handling, stated in `derivation_rationale`. `tests/test_templates.py::test_attribution_weight_null_imperfection_is_exercised`. Also brings `member_status` into coverage. |
| C | **T4 has no GROUP BY.** | All 6 T4 families now `GROUP BY` a real dimension (channel / priority / category / member_status / status / engagement_type). `tests/test_templates.py::test_t4_families_are_grouped_aggregations`. |
| D | **Campaign-window questions absent.** `start_date / end_date / primary_channel` still unused. | Deferred — the delivered dataset's campaign windows overlap the whole timeline, so an "active during period X" question has low discriminative value. Left for a future revision. |
| E | **Rephrase groups planned only.** | `generator/rephrase.py` renders each of the 8 groups' base family with a fixed parameter, verifies once, and attaches reworded variants that share the identical SQL / answer / `result_hash`. Output: `rephrase/<profile>/crm_rephrase_pairs.csv` + `crm_rephrase_map.csv` (companion, not part of the 160). `tests/test_rephrase.py` asserts one hash per group. |
| F | **No real independent review.** | Still a process step: a reviewer runs `pytest tests/` + a clean-room `generate_crm` from a separate checkout and signs `crm_taxonomy_review_checklist.md`. |
| G | **ERD says "Draft v0.2".** | Track A (bundle owner) to freeze it. Out of Track B's control; flagged. |

## Refactor (ponytail "minimum viable")

- Family specs moved from a 330-line Python dict to declarative
  `generator/families.json`. `scale_pairs.py`: 506 → 180 lines.
- Shared `generator/verify.py` (execute + serialize + log) replaces the
  duplicated machinery in `scale_pairs.py` and `author_qa_pairs.py`.
- `crm_question_family_matrix.csv` is now **generated** from
  `families.json` (`generator/gen_family_matrix.py`) — one source of truth.
- No new dependency added; the ponytail npm plugin was not installed.

## Field coverage now

Added since the review: `support_cases.sla_due_at / resolved_at`,
`contact_campaigns.member_status / attribution_weight`.

Still unused: `support_cases.opened_at`, `campaigns.start_date /
end_date / primary_channel`, `contact_campaigns.is_primary_attribution /
first_touch_at / last_touch_at`.
