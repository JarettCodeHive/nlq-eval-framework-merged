# Sales — Judge Failure Analysis (`v1.0.0-full-fixed2`)

**Run:** `release/v1.0.0-full-fixed2/sales/judge/20261012T082108Z/results.json`
**Exact match:** 137 pass / 42 fail of 179 eligible — **76.54%** pass rate.
**Target:** ≥90% per domain. **Gap:** 13.46 points, i.e. at least 25 more of
the 42 failures need to flip to pass to clear the bar.

Every failing row was read individually (expected vs. actual answer,
`exact_match_detail`, the platform's generated SQL, and the judge's
independent `factual_correctness` rationale as a cross-check for whether the
platform's content was substantively right or wrong). **None of the 42 are
fixable by changing `judge/exact_match.py`.** All four categories below are
either a platform SQL-generation defect, a platform behavioral gap, or a
dataset-encoding choice that misleads the platform's own SQL generation —
not a scorer bug.

---

## Category 1 — Enum/categorical literal mismatch between dataset encoding and the platform's guessed SQL literal (17 — **not scorer-fixable; dataset-encoding issue**)

The Sales dataset stores several categorical columns as machine tokens
(`lead_source = 'ColdCall'`, `'EventBooth'`, `'WebForm'`) or with casing that
differs from how a reader would naturally phrase them in a `WHERE` clause.
Pulse translates the question's wording into a human-readable guess —
`lead_source ILIKE '%cold call%'` (with a space) or
`quote_status IN ('accepted', ...)` (lowercase) — which `ILIKE`/`IN` then
fails to match against the real stored tokens `'ColdCall'` / `'Accepted'`.
The result is a systematic `0` or `NULL` for every question that filters on
one of these columns, even though the underlying rows exist.

Confirmed by comparing `platform_generated_sql` against
`qa_pairs/sales_qa_pairs.csv`'s `reference_sql` for three of these directly:

- `SALES-T1-03-10` ("...cold call source?") — reference: `WHERE lead_source = 'ColdCall'`.
  Platform: `WHERE lead_source ILIKE '%cold call%'` → 0 rows. Expected `3025`,
  platform answered `0`. Same mechanism for `SALES-T1-03-11` (`EventBooth`,
  expected `3010`) and `SALES-T1-03-14` (`WebForm`, expected `2969`).
- `SALES-T2-08-35` ("...percentage were accepted?") — reference:
  `WHERE q.quote_status = 'Accepted'` (capitalized). Platform:
  `WHERE q.quote_status IN ('accepted', 'rejected', 'expired')` (lowercase) →
  0 decided lines, 0 accepted, "percentage cannot be calculated". Expected
  `32.99`.
- `SALES-T4-03-09` / `SALES-T4-06-23` ("...deals from cold call leads...") —
  same `ColdCall` mismatch, cascading into a `0`-row breakdown by product
  category where the expected answer has four non-zero categories.

Every question in this category follows the same shape: the platform's
narrative is coherent and internally consistent ("no leads were found... this
could mean the source isn't tracked under that label") — it is reasoning
correctly from a query that returned nothing, it just built the wrong query.
Judge `factual_correctness` scores these 1 across the board, agreeing the
answer is wrong, for the same reason the deterministic scorer does.

**Full list (17):** `SALES-T1-03-10`, `SALES-T1-03-11`, `SALES-T1-03-14`,
`SALES-T1-06-22`, `SALES-T1-06-23`, `SALES-T1-06-26`, `SALES-T2-08-35`,
`SALES-T2-10-38`, `SALES-T3-05-28`, `SALES-T3-05-29`, `SALES-T4-03-09`,
`SALES-T4-03-13`, `SALES-T4-06-23`, `SALES-T4-06-27`, `SALES-RG-02-V01`,
`SALES-RG-07-V01`, `SALES-RG-15-V01`.

**Resolution path:** nothing to change in `judge/exact_match.py` — the
scorer is reading the platform's final text correctly; the text itself is
wrong because the SQL behind it is wrong. The only lever this codebase has
is the *dataset's* encoding of these enum values (e.g. storing
`lead_source` as `"Cold Call"` with a space, or `quote_status` consistently
lower/upper-cased to match what a model is likely to guess) — a dataset
regeneration decision, not a quick patch, and the same open question
flagged for Logistics/Project Management's CamelCase values. See the
cross-domain common-patterns file for the consolidated recommendation.

---

## Category 2 — Platform SQL logic defects: genuinely wrong computation (18 — **not fixable in this codebase at all**)

The platform's own SQL joins, filters, or date-boundary logic produce a
materially different number from the ground truth, with no shared
mechanism this codebase controls:

- **`SALES-T3-02-09`** — "How many new leads have never become a deal?"
  Expected `2228`, platform answered `10000` (essentially every lead). The
  platform's SQL is `WHERE NOT EXISTS (SELECT 1 FROM studio.deals d WHERE
  d.lead_id = l.lead_id)` — if `deals.lead_id` isn't the column that
  actually links a deal back to its originating lead (or is sparsely
  populated), the anti-join matches almost everything. Judge
  `factual_correctness` = 1, agreeing.
- **`SALES-T5-03-12`** — North-territory Q1→Q2 won-deal comparison. Expected
  `6,754,906.82 | 210,657.55`; platform answered **"~$2.05 billion" / "~$64.9
  million"** — roughly 300x too large. The platform's SQL joins
  `deals d JOIN leads l ON l.rep_name = d.rep_name` — a join on a name
  string rather than an id. If any rep name is not unique across the
  dataset, this fans out into massive double/triple counting, and North is
  the one territory where it happened at this scale. Judge
  `factual_correctness` = 1.
- **`SALES-T4-04-14`..`18`** (5 questions, one per territory, won-deal-by-
  target-period breakdown) — partial mismatches on specific periods
  (`platform_generated_sql` joins `targets t ON t.rep_name = w.rep_name`,
  the same name-join pattern as above, which can silently fan out a
  period's total when a rep's target row isn't uniquely keyed). Judge scores
  these 2-3 (partially right), consistent with "most periods compute
  correctly, specific ones don't."
- **`SALES-T5-02-06`, `-08`, `-09`** (rep-quota-shortfall lists by
  territory) — platform finds fewer reps below quota than expected, or
  computes a different shortfall amount for one or two reps on the list.
  Judge factual_correctness 1-2.
- **`SALES-T5-05-19`..`-22`, `SALES-T5-06-23`, `SALES-RG-14-V01`** (6
  questions, Q1→Q2 quoted-revenue-by-product-category) — consistently ~10-15%
  lower than expected on both quarters (e.g. hardware: platform `$878.4M` Q1
  vs expected `$984.0M`). The platform's SQL filters on `quoted_at` date
  rather than the deal's `close_date`; a consistent percentage-size gap
  across every category suggests a systematic date-window difference
  between "when quoted" and "when the deal closed," not a one-off error.
  `SALES-T5-06-23` ("which category had the best trend") inherits this and
  picks a different "best" category as a direct consequence.
- **`SALES-T3-03-15`** (West-territory zero-amount target periods: `23` vs
  expected `24`) and **`SALES-T2-09-37`** (Won deals in target period: `945`
  vs expected `940`) — small off-by-a-few discrepancies, same family of
  boundary/join edge cases as above, too small individually to diagnose
  further without the platform's full statement set.

**Full list (18):** `SALES-T2-09-37`, `SALES-T3-02-09`, `SALES-T3-03-15`,
`SALES-T4-04-14`, `SALES-T4-04-15`, `SALES-T4-04-16`, `SALES-T4-04-17`,
`SALES-T4-04-18`, `SALES-T5-02-06`, `SALES-T5-02-08`, `SALES-T5-02-09`,
`SALES-T5-03-12`, `SALES-T5-05-19`, `SALES-T5-05-20`, `SALES-T5-05-21`,
`SALES-T5-05-22`, `SALES-T5-06-23`, `SALES-RG-14-V01`.

**Resolution path:** none available in this repository. The deterministic
scorer and the dataset are both correct here; the platform's query planning
is the defect, and this evaluation framework only observes the platform's
final answer — it cannot change how Pulse writes SQL.

---

## Category 3 — Platform never volunteers the percentage-change figure for Q1→Q2 trend questions (6 — **not a scorer bug; a dataset/question-design gap**)

Every "how did X change from Q1 2026 to Q2 2026" question's `expected_answer`
is a 5-field record: `Q1 | Q2 | absolute_change | percentage_change |
direction`. In every failure in this category, the platform states Q1, Q2,
and the absolute dollar change correctly (judge `factual_correctness` 4-5,
confirming the core numbers are right) — but it **never states the
percentage** anywhere in the response, even though the question never
explicitly asked for one. The scorer correctly reports the percentage value
as absent, because it is absent.

- `SALES-T5-03-10`: expected `...| -90.65 | down`. Platform: "...a drop of
  $2,158,521.67..." — no percentage anywhere in ~250 words of prose.
- `SALES-RG-13-V01` additionally shows a secondary, scorer-side wrinkle: the
  word "down"-equivalent appears nowhere near the money figures (`label
  'down' present but 3328 chars from any of its values (limit 120)`) in a
  very long answer — but fixing that proximity check would not flip this row
  to pass on its own, since the percentage is still missing.
- `SALES-RG-13-V02` scores judge `factual_correctness` = 5 (the platform's
  framing — "No, [they] closed less... a decline of $2,158,521.67" — is
  judged a fully correct answer to the question as asked) while still
  failing exact-match for the same missing-percentage reason.

**Full list (6):** `SALES-T5-03-10`, `SALES-T5-03-11`, `SALES-T5-03-13`,
`SALES-T5-03-14`, `SALES-RG-13-V01`, `SALES-RG-13-V02`.

**Resolution path:** not fixable in `judge/exact_match.py` — there is no
percentage substring in the platform's text to find. The only lever is
dataset/pair design: either phrase these questions to explicitly ask "...and
by what percentage?" (so the platform is more likely to volunteer the
number), or drop the percentage field from `expected_answer` for this
question family and rely on the absolute change + direction instead. That's
a Q&A-pair content decision, not a code change, and out of scope for "fix
what's resolvable in the codebase."

---

## Category 4 — Long answer compresses a list's tail into a range instead of enumerating every value (1 — **not fixable in this codebase**)

`SALES-RG-12-V01` ("Which reps in the Central territory missed quota...and
by how much?") — expected 8 labelled values. The platform states the first
5 reps individually, then "three others (Cheryl Harris, Justin Owens, and
Jacob Kelly) also missing, in the roughly $63,000 to $96,000 range" — the
three correct dollar figures (`95553.00`, `79515.89`, `63206.68`) are simply
never written down individually anywhere in the text; only a summarizing
range is given. Judge `factual_correctness` = 4 (all stated figures are
exactly right; it doesn't penalize the stylistic compression the way
exact-match's completeness check does).

**Resolution path:** not fixable in this codebase — there is nothing for the
scorer to find, because the exact values were never printed by the
platform. This is a platform response-style choice (likely length-driven
summarization on long lists).

---

## Summary

| Category | Count | Fixability |
|---|---|---|
| 1. Enum/categorical literal mismatch (dataset encoding vs. platform-guessed SQL literal) | 17 | Not scorer-fixable; dataset-encoding decision only |
| 2. Platform SQL logic defects (joins/filters/date boundaries) — genuinely wrong computation | 18 | Not fixable in this codebase |
| 3. Platform omits percentage-change figure for trend questions | 6 | Not a scorer bug; dataset/question-design decision |
| 4. Long list tail compressed into a range, not enumerated | 1 | Not fixable in this codebase |
| **Total** | **42** | **0 of 42 fixable by changing `judge/exact_match.py`** |

Of the 42 Sales failures, 25 (Category 2 + the uncategorizable fraction of
Category 1 that would need a full re-run even after a dataset fix) are
pure platform/query-planning defects outside this repository's control.
17 (Category 1) trace to a dataset-encoding choice (CamelCase/space-free or
inconsistently-cased enum tokens) that this repository *could* change, but
only via a dataset regeneration — not a scorer or quick-code fix. 6
(Category 3) and 1 (Category 4) are platform response-style gaps relative
to the pair's exact-match contract, not platform factual errors and not
scorer bugs. **No change to `judge/exact_match.py` would move Sales's pass
rate** — see the cross-domain common-patterns file for whether Category 1's
dataset-encoding fix is worth doing once, centrally, across domains.
