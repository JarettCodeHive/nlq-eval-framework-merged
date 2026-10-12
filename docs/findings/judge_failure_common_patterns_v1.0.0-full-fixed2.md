# Cross-Domain Judge Failure Patterns — `release/v1.0.0-full-fixed2`

Synthesized from the five per-domain analyses below, each of which classified
every `exact_match` FAIL row for its domain's judge run. Read each one for
the full question-by-question detail; this file only covers what recurs
across domains and the resulting priority order for action.

- [`crm/crm_judge_failure_analysis_v1.0.0-full-fixed2.md`](crm/crm_judge_failure_analysis_v1.0.0-full-fixed2.md)
- [`sales/sales_judge_failure_analysis_v1.0.0-full-fixed2.md`](sales/sales_judge_failure_analysis_v1.0.0-full-fixed2.md)
- [`finance/finance_judge_failure_analysis_v1.0.0-full-fixed2.md`](finance/finance_judge_failure_analysis_v1.0.0-full-fixed2.md)
- [`logistics/logistics_judge_failure_analysis_v1.0.0-full-fixed2.md`](logistics/logistics_judge_failure_analysis_v1.0.0-full-fixed2.md)
- [`project_management/project_management_judge_failure_analysis_v1.0.0-full-fixed2.md`](project_management/project_management_judge_failure_analysis_v1.0.0-full-fixed2.md)

## 0. Where every domain stands against the ≥90% target

| Domain | Eligible | Pass | Fail | Pass % | Gap to 90% |
|---|---|---|---|---|---|
| CRM | 179 | 99 | 80 | 55.31% | 34.69pp |
| Sales | 179 | 137 | 42 | 76.54% | 13.46pp |
| Finance | 179 | 144 | 35 | 80.45% | 9.55pp |
| Logistics | 180 | 164 | 16 | **91.11%** | already met |
| Project Management | 179 | 125 | 54 | 69.83% | 20.17pp |
| **Total** | **896** | **669** | **227** | **74.66%** | — |

## 1. Headline finding

**Across 227 total failures, only 11 are cleanly fixable inside this
codebase** (10 in `judge/exact_match.py`, 1 in `qa_pairs/` reference SQL),
and every domain-level fork independently reached the same structural
conclusion by the same method: cross-checking each `exact_match` FAIL
against the LLM judge's *independent* `factual_correctness` score. When the
judge also scores the answer low with a rationale agreeing the platform's
number is wrong, that is strong, repeatable evidence the gap is the live
Pulse platform's answer quality — not this project's scorer, dataset, or
Q&A pairs. That was true for the overwhelming majority of failures in all
five domains, most starkly in CRM (80/80 platform-only, confirmed by the
judge agreeing in 79 of 80 cases) and Logistics (14/16 platform-only).

**Fixing everything in this codebase's control does not close the gap to
90% in CRM, Sales, or Project Management.** Finance's own fork quantified
this precisely: closing its 9 scorer-fixable + 2 dataset-defect failures
moves Finance from 80.45% to 86.6% — still short. The remaining gap in
every domain below target is dominated by the live platform's own
computation and response-completeness quality, which is outside this
repository's reach by design (`judge/README.md`: "the platform under
evaluation is an agent, not a model").

## 2. Patterns that recur across domains

### Pattern A — Trend/quarter-comparison records: platform omits the derived delta or percentage field

The shared `Q1 | Q2 | change | pct_change | direction` question shape (T5
tier) is reused across domains, and the platform systematically states Q1,
Q2, and the direction word correctly, then either omits the percentage/delta
figure entirely or states it unsigned.

| Domain | Omitted entirely | Sign stripped to unsigned magnitude |
|---|---|---|
| Sales | 6 | — |
| Logistics | 9 | 1 (scorer-fixable) |
| Finance | 1 | 1 (scorer-fixable) |
| Project Management | 11 (incl. 1 scorer code bug, see §3) | — |
| **Total** | **27** | **2** |

The *omission* half (27 failures) is platform-only — the scorer cannot pass
a value never stated. The *unsigned-magnitude* half (2 failures, Logistics +
Finance) is a real, narrow, generalizable gap in `judge/exact_match.py`:
the existing sign-flexible fallback for negative numerics in trend records
only covers one field of the record, not every numeric field symmetrically.
See R1 in §3.

### Pattern B — CamelCase / no-space categorical literals stored in the dataset

Confirmed mechanism (Sales fork verified it by diffing `platform_generated_sql`
against `reference_sql`): categorical columns are stored as compound tokens
(`lead_source='ColdCall'`, `'EventBooth'`) or inconsistent casing
(`quote_status='Accepted'`). Pulse's own generated SQL guesses the
natural-language rendering (`ILIKE '%cold call%'`), which never matches the
stored literal, and the query returns zero/null rows.

- **Sales:** 17 failures, confirmed mechanism.
- **Project Management:** 22 failures with the identical signature — "zero
  result, multi-word status/location phrase not translated" (`InProgress`,
  `NotStarted`, `OnHold`, `AsiaPacific`, `NorthAmerica`). The PM fork
  classified this as platform-side; it is more precisely the *same
  dataset-encoding root cause as Sales's Pattern B*, not an independent
  platform defect — flagging the correction here since the two forks ran
  independently and used different wording for what is one mechanism.

**This is the single largest fixable-adjacent cluster in the whole analysis
— 39 of 227 failures (17%) — but it is not a scorer fix.** The platform's
SQL is arguably *reasonable* given what a human would type; the dataset's
encoding is what breaks the match. See R4 in §3 — this is a known, deferred
decision from earlier in this engagement (CamelCase dataset-value rename
for Logistics/PM was already flagged as "a bigger, separate operation
requiring explicit go-ahead" before this analysis), now with hard evidence
it costs ~39 passing questions across just two domains, and worth checking
in Finance/Logistics/CRM too — this analysis round did not find it reported
in those three, which is itself useful evidence their categorical columns
may already be human-readable.

### Pattern C — CamelCase/compound labels in the platform's *own prose* not recognized by the label matcher

Distinct from Pattern B: here the platform's SQL and computed values are
exactly right, and the platform states a correctly-rendered label in its
prose (e.g. "Long-Term Debt"), but the scorer's `_find_label()` doesn't
recognize the hyphen as a valid word-separator the way it already treats
spaces.

- **Finance:** 1 failure (`FIN-T5-04-16`, `LongTermDebt` → "Long-Term Debt").

Same mechanism class as the underscore/hyphen-flexible matching fix already
shipped for Logistics/Project Management earlier in this engagement — this
is the one case this round found where the existing fallback's coverage
(spaces only) is one character class short of hyphens. Low-risk, scorer-only.
See R2 in §3.

### Pattern D — Multi-value breakdown computed via a different join path, producing systematically-scaled-wrong values

The platform's own query plan diverges from `reference_sql`'s join path for
multi-dimensional breakdowns, producing a consistent (not random) scaling
error rather than noise.

- **CRM:** 62 failures — two named, consistent multipliers found:
  support-case-by-contact breakdowns ~1.84–1.93× too high (consistent with
  a join producing one row per contact↔account relationship instead of one
  row per case); engagement-by-channel breakdowns ~0.85× too low.
- **Finance:** 13 failures, concentrated in FX-adjusted quarter comparisons
  and budget/ledger-activity-presence questions — verified against
  `reference_sql` re-executed in DuckDB; the dataset and pair are clean,
  the platform's own FX/join computation is what differs.
- **Sales:** 18 "platform SQL logic defect" failures (joins/filters/date
  boundaries).
- **Logistics:** 1 (quarters swapped/zeroed).

This is the largest single category overall (~94 of 227 failures, 41%) and
is **not fixable in this codebase under any classification** — it is the
live platform's query-generation quality for multi-dimensional aggregation,
and CRM's fork confirms the judge agrees the platform is wrong in
essentially every instance (76/80 scored `factual_correctness=1`).

### Pattern E — T3 outer-join / scope-filter silently dropped

The exact weakness `judge/README.md` already names and expects to recur
("T3 NULL handling, §9.2"): a `LEFT JOIN ... WHERE <child> IS NULL` question,
or an equivalent scoped filter, comes back answered as if the filter or
outer-join weren't applied — the platform states a materially different
(usually larger, "no exceptions found") figure.

- **Finance:** 4 (two LEFT JOIN variants verified at 132/4868/1264 against
  DuckDB re-execution; one scope-filter-dropped variant where the platform
  answered for the whole table instead of a `rate_source`-filtered subset).
- **Logistics:** 1 (NULL rows counted as orphan rows — NOT EXISTS vs. NULL
  semantics confused).
- **Project Management:** 2 (platform drops a stated filter, returns the
  global aggregate instead).

This confirms a prediction already in the codebase's own documentation
rather than surfacing something new — `judge/results.json`'s dedicated
`null_handling` diagnostic exists precisely because this was expected.
Platform-only; not actionable here.

### Pattern F — Required full breakdown truncated to top-N, remainder summarized in prose

The platform gets the top handful of a ranked/labelled breakdown exactly
right, then switches to prose ("...and other subtypes contributing smaller
amounts") instead of enumerating every required value.

- **CRM:** 1 (5 of 7 ranked items).
- **Sales:** 1 (long list tail compressed into a range).
- **Finance:** 5 (top 5 of 15–17 subtypes, judge scores it `3` — "correctly
  states the top 5... lists remaining names without values").

Platform-only; nothing in the response contains the missing values for the
scorer to find.

### Pattern G — Internal/unspeakable schema-derived labels in the Q&A pair contract

- **Finance:** 7 (`has_actuals`/`no_actuals` — internal field names from the
  generator, not words a human or the platform would ever say back; the
  judge independently confirms the platform's numbers are exactly right in
  every one).

This is a pair-authoring defect, not a scorer or platform defect — fixable
by renaming the labels in `qa_pairs/generator/finance/families.json` to
natural language (e.g. "with ledger activity" / "without ledger activity"),
the way every other labelled-list question already does. No other domain's
fork reported this pattern, but it's worth a quick preventive check of each
domain's `families.json` for the same internal-token-as-label shape, since
it is cheap to introduce and easy to miss during authoring. See R3 in §3.

### Pattern H — Reference-date drift

- **Project Management:** 7 — datasets were generated against a frozen
  `reference_today=2026-08-01` (§HC-7 determinism requirement), but this
  judge run executed on 2026-10-11, over two months later. "Overdue"/
  "current"-style counts computed by the platform against *its own* notion
  of "today" diverge from the frozen `expected_answer`.

Not a code bug — it's a consequence of determinism (frozen `reference_today`)
colliding with a judge run executed long after dataset generation. Not
reported by other domains' forks, but any domain with an "overdue," "as of
today," or "currently active" question family is structurally exposed to
the same drift the longer the gap between `build-dataset` and the judge
run. See R5 in §3.

### Pattern I — Known hard scorer limitation: substring label with repeated occurrences in long prose

- **Project Management:** 11 — `non_billable` is a word-superstring of
  `billable`, which also recurs multiple times in the same long response;
  the ownership/attachment-distance heuristic in `_label_owns_a_value()` /
  `_attachment_cost()` cannot reliably tell which occurrence is the
  structurally "real" pairing. This exact case was investigated earlier in
  this engagement and a fix was deliberately **not** shipped because every
  attempted heuristic was itself unreliable and risked introducing new
  false passes — it recurs unchanged in this run.
- **Logistics:** 1 — a related but distinct case: label-to-value attachment
  distance exceeded in a long, multi-section response (the 120-character
  proximity window in the comparison policy).

Still an open, acknowledged hard problem, not a quick fix — see R7 (do not
attempt without a materially better algorithm).

### Pattern J — Platform self-contradicts within one response

- **Logistics:** 2 — platform states a figure is "missing" in one part of
  its answer while having computed the same figure correctly elsewhere in
  the same response.
- **Project Management:** 1 — platform's headline figure contradicts its
  own stated breakdown.
- Arguably overlaps with part of **CRM's** 12 "zero/null for a non-zero
  ground truth" failures, where the platform explicitly states "no data
  found" in prose rather than silently guessing wrong.

A platform reasoning/synthesis issue, not fixable here.

## 3. Recommendations, in priority order

| # | Action | Where | Effort | Payoff |
|---|---|---|---|---|
| **R1** | Extend the sign-flexible/magnitude fallback for negative numerics in trend records to cover **every** numeric field in the record symmetrically, not just one | `judge/exact_match.py` | Low — code only | Confirmed +2 (Logistics, Finance); audit Sales/PM/CRM T5 families for the same shape |
| **R2** | Extend `_find_label()`'s whitespace-insensitive fallback to also treat hyphens as an inserted separator, matching the existing space-handling | `judge/exact_match.py` | Low — code only | Confirmed +1 (Finance); same mechanism class as the earlier underscore fix for Logistics/PM |
| **R3** | Rename internal/unspeakable schema-derived labels (`has_actuals`/`no_actuals`) to natural-language labels; preventively audit all five domains' `families.json` for the same shape | `qa_pairs/generator/finance/` (+ audit elsewhere) | Low-medium — authoring only | Confirmed +7 (Finance); preventive value elsewhere unverified |
| **R4** | Decide whether to re-encode CamelCase/no-space categorical literals dataset-wide as human-readable strings (or add a synonym/alias layer at ingestion) | `generators/sales`, `generators/project_management` (+ check logistics/finance/crm) | High — full dataset + Q&A regeneration + a fresh live judge run per affected domain | Largest single lever found — 39 confirmed failures (Sales 17, PM 22) — but this is the same big, deferred decision flagged earlier in this engagement; needs explicit go-ahead, not a quick patch |
| **R5** | Re-peg or regenerate `reference_today`-anchored "overdue"/"current" question families closer to each judge run, or stop freezing `reference_today` for just those families | `qa_pairs/generator/project_management/` process (+ check other domains for similar families) | Low-medium — process, not code | Confirmed +7 (PM) |
| **R6** | Fix `reference_sql` cardinality for the FX-rate-match-count question family to `COUNT(DISTINCT ...)` so it matches the question's per-row wording, then regenerate `expected_answer` | `qa_pairs/generator/finance/` | Low — one query template | Confirmed +2 (Finance) |
| **R7** | Do not attempt without a materially better algorithm: the substring/repeated-occurrence label-ownership case (Pattern I, 12 failures). Previously investigated and correctly left unresolved; still true | `judge/exact_match.py` | — | Explicitly declined, documented risk of new false passes |
| — | No action available in this codebase: Pattern D (join-path divergence, ~94 failures), Pattern E (T3 filter/outer-join dropped, 7 failures — already a named, expected diagnostic), Pattern F (breakdown truncation, 7 failures), Pattern J (self-contradiction, 3 failures), the 27-failure omission half of Pattern A | Platform (Pulse) | — | Flag to the platform owner as named clusters; not ours to fix |

**R1 + R2 + R3 + R6 together are low-risk, codebase-only, and close exactly
11 of 227 failures** (confirmed, not projected) — consistent with the
individual domain forks' own math (e.g. Finance 80.45% → 86.6%). **R4 and
R5 are the two highest-leverage remaining items (46 more failures between
them) but both require explicit scope decisions and dataset/Q&A
regeneration, not a quick patch** — flagging for a decision rather than
proceeding unilaterally, consistent with how the equivalent CamelCase
dataset question was handled earlier in this engagement.

## 4. What this does and doesn't mean for the ≥90% target

| Domain | Current | + confirmed in-codebase fixes (R1-R3, R6) | Still short of 90%? |
|---|---|---|---|
| CRM | 55.31% | 55.31% (0 applicable) | Yes — by a wide margin; the gap is Pattern D/J, platform-only |
| Sales | 76.54% | 76.54% (0 applicable; R4 would add 17 → 86.03%, still a dataset-regen decision) | Yes |
| Finance | 80.45% | 86.59% (+9 scorer, +2 R6) | Yes, narrowly |
| Logistics | 91.11% | 91.67% (+1 R1) | Already met; small further gain |
| Project Management | 69.83% | 69.83% (0 applicable here; R4+R5 would add 29 → 86.03%, both decisions, not patches) | Yes |

No domain reaches 90% from codebase-only fixes alone except Logistics, which
was already there. Reaching 90% in CRM, Sales, Finance, or Project
Management requires either the live platform's own answer quality to
improve, or the R4/R5 dataset-level decisions to be made and executed —
neither is a `judge/exact_match.py` patch.
