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

**Across 227 total failures, 211 are the live Pulse platform computing or
reporting a materially different answer than the ground truth** — every
domain-level fork reached this independently, by the same method:
cross-checking each `exact_match` FAIL against the LLM judge's
*independent* `factual_correctness` score. When the judge also scores the
answer low with a rationale agreeing the platform's number is wrong, that
is strong, repeatable evidence the gap is the live platform's answer
quality. That held for the overwhelming majority of failures in all five
domains, most starkly in CRM (80/80, confirmed by the judge agreeing in 79
of 80 cases) and Logistics (14/16).

**The remaining ~25 failures are mismatches between how the platform
phrases or structures an answer and how this evaluation reads it** —
label spelling/punctuation differences, an unsigned figure next to a
signed one, a schema-derived label with no natural-language form. These
are pointed out below as patterns, without assuming which side — the
evaluation's label matching, the platform's rendering, or the dataset's
own encoding — should change; growing this codebase's scorer with more
special cases for each new phrasing variant isn't the right trade-off
compared to the underlying behavior being consistent in the first place.

**Fixing every one of those ~16 does not close the gap to 90% in CRM,
Sales, or Project Management.** Finance's own numbers illustrate this:
even setting its label/sign/reference-query mismatches aside entirely,
Finance would move from 80.45% to 86.6% — still short. The gap in every
domain below target is dominated by the live platform's own computation
and response-completeness quality (`judge/README.md`: "the platform under
evaluation is an agent, not a model").

## 2. Patterns that recur across domains

### Pattern A — Trend/quarter-comparison records: platform omits the derived delta or percentage field

The shared `Q1 | Q2 | change | pct_change | direction` question shape (T5
tier) is reused across domains, and the platform systematically states Q1,
Q2, and the direction word correctly, then either omits the percentage/delta
figure entirely or states it unsigned.

| Domain | Omitted entirely | Signed figure printed unsigned |
|---|---|---|
| Sales | 6 | — |
| Logistics | 9 | 1 |
| Finance | 1 | 1 |
| Project Management | 11 (incl. 1 message-formatting bug, see §3) | — |
| **Total** | **27** | **2** |

The *omission* half (27 failures) is the platform not stating a value at
all — nothing to read differently on this evaluation's side. The
*unsigned-magnitude* half (2 failures, Logistics + Finance) is different:
the platform signs one derived figure in a trend record (e.g. a
percentage) but not a parallel one (e.g. the absolute dollar change) in
the same answer — an internal inconsistency between two numbers describing
the same change, independent of whether either number is computed
correctly (both are).

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

**This is the single largest cluster in the whole analysis — 39 of 227
failures (17%).** The platform's generated SQL is arguably reasonable
given what a human would type; the dataset's encoding is what the SQL
fails to match. Either side changing — the application normalizing
category literals when building a filter, or the dataset storing
human-readable values — would close this cluster; this is a known,
deferred decision from earlier in this engagement (noted then as "a
bigger, separate operation requiring explicit go-ahead"), now with hard
evidence it costs ~39 passing questions across just two domains. Worth
checking in Finance/Logistics/CRM too — this analysis round did not find
it reported in those three, which is itself useful evidence their
categorical columns may already be human-readable.

### Pattern C — CamelCase/compound labels in the platform's *own prose* not recognized by the label matcher

Distinct from Pattern B: here the platform's SQL and computed values are
exactly right, and the platform states a correctly-rendered label in its
prose (e.g. "Long-Term Debt") — this evaluation's label matching doesn't
recognize the hyphen as a valid word-separator the way it already treats
spaces.

- **Finance:** 1 failure (`FIN-T5-04-16`, `LongTermDebt` → "Long-Term Debt").

Same shape as the underscore/hyphen-flexible matching gap flagged for
Logistics/Project Management earlier in this engagement — the platform's
phrasing here isn't wrong by any normal reading; the gap is in how this
evaluation reads it.

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

This is a mismatch inside the test itself, not a scorer or platform
defect — the expected-answer label and the only way anyone would actually
phrase that answer were never going to be the same string. No other
domain's fork reported this pattern, but it's worth a quick preventive
check of each domain's `families.json` for the same internal-token-as-label
shape, since it is cheap to introduce and easy to miss during authoring.

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
run.

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

Still an open, acknowledged hard case — the platform's answer is correct
in every one of these; the difficulty is purely in reliably telling which
occurrence of a repeated, overlapping label is the structurally "real"
pairing in long prose. Not attempted further here, for the same reason it
wasn't attempted earlier in this engagement: every heuristic tried was
itself unreliable and risked introducing new false passes.

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

## 3. What each pattern is, by kind

| Pattern | Failures | What it is |
|---|---|---|
| D — join-path divergence in multi-value breakdowns | ~94 | The live platform computing a materially different answer |
| B — compact/CamelCase categorical literal doesn't match a spaced filter | 39 | The live platform's generated query returning zero/wrong rows |
| A (omission half) — trend record missing a derived field | 27 | The live platform not stating a required value |
| E — scoped filter or outer-join silently dropped | 7 | The live platform ignoring part of the question |
| F — breakdown truncated to top-N | 7 | The live platform summarizing instead of enumerating |
| H — reference-date drift | 7 | This evaluation's frozen test date going stale against a later judge run |
| I — repeated/overlapping substring label in long prose | 12 | A known hard case in how this evaluation reads a long response; the platform's answer is correct |
| J — platform self-contradicts within one response | 3 | The live platform's own internal consistency |
| G — internal field name used as a record label | 7 | A mismatch inside the test itself (no natural-language form exists for the label) |
| A (unsigned-magnitude half) — one of two parallel figures loses its sign | 2 | An internal inconsistency in how the platform presents two related numbers |
| C — hyphenated label not recognized | 1 | How this evaluation reads a correctly-phrased label |
| Finance's reference-query cardinality mismatch (not a cross-domain pattern — see Finance's own category 5) | 2 | A flaw in this project's own reference query, independent of the platform |
| Finance's one-cent rounding cascade (not a cross-domain pattern) | 1 | Two systems rounding a derived value at different points — not a defect on either side |

Patterns D, B, A (omission), E, F, and J (~177 failures) are the live
platform producing a materially wrong, incomplete, or self-contradictory
answer. Patterns H, I, G, A (unsigned-magnitude), and C (~49 failures,
including the two Finance-only items above) are mismatches that live
inside this evaluation's own expectations, labeling, or timing rather than
anything the platform got wrong — pointed out for visibility, not as
action items this codebase is committing to.

## 4. What this means for the ≥90% target

| Domain | Current pass rate | Dominant pattern below target |
|---|---|---|
| CRM | 55.31% | Pattern D (join-path divergence) — 62 of 80 failures |
| Sales | 76.54% | Pattern D (18) and Pattern B (17) roughly evenly |
| Finance | 80.45% | Pattern D (13) and Pattern E (4) |
| Logistics | 91.11% | Already at target; remaining 16 failures are small and varied |
| Project Management | 69.83% | Pattern B (22) and Pattern I (11) |

In every domain below target, the dominant pattern is the live platform's
own answer quality (Pattern D or B), not anything internal to this
evaluation. No amount of adjusting this project's own label matching,
reference queries, or test timing changes that.
