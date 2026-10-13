# Finance Judge Failure Analysis — `release/v1.0.0-full-fixed2`

**Run:** `release/v1.0.0-full-fixed2/finance/judge/20261012T002452Z/results.json`

**Exact match:** eligible 179, pass 144, fail 35, **pass_pct 80.45%**

**Target:** ≥ 90% — gap: 35 failures. 23 of them are the live platform
computing or reporting a materially different answer than the ground
truth (categories 4, 6, 7, 8); the remaining 12 are mismatches between
the platform's phrasing and this evaluation's own expectations (categories
1–3) or a flaw in this project's own reference query (category 5) —
pointed out below without assuming which side should change.

Every number below was cross-checked by re-running each failing question's
`reference_sql` directly against the exported CSVs in
`release/v1.0.0-full-fixed2/finance/dataset/` with DuckDB — not assumed from
the CSV's `expected_answer` column alone. Where that re-run reproduced
`expected_answer` exactly, the ground truth is confirmed and any mismatch is
the platform's; this surfaced one case (§5) where it is not.

**Plain-language, root-cause write-ups** —
one per category below, confirmed against the actual dataset:

- [Category 1 — an internal field name used as a record label](finance_category1_internal_label_v1.0.0-full-fixed2.md)
- [Category 2 — a hyphenated label isn't recognized](finance_category2_hyphenated_label_v1.0.0-full-fixed2.md)
- [Category 3 — a negative value loses its sign](finance_category3_unsigned_delta_v1.0.0-full-fixed2.md)
- [Category 4 — one field missing from an otherwise-correct answer](finance_category4_missing_field_v1.0.0-full-fixed2.md)
- [Category 6 — a stated filter gets dropped](finance_category6_dropped_filter_v1.0.0-full-fixed2.md)
- [Category 7 — breakdown cut short at top 5](finance_category7_truncated_breakdown_v1.0.0-full-fixed2.md)
- [Category 8 — materially wrong totals](finance_category8_wrong_totals_v1.0.0-full-fixed2.md)

---

## Category 1 — An internal field name used as a record label (7)

`has_actuals` / `no_actuals` are the field names the Q&A pair generator used
as record labels, not words a human (or an LLM platform) would ever say back.
In every one of these 7 failures the judge independently confirms the
platform's **numbers are exactly right** — this is purely
`judge/exact_match.py`'s `_find_label()` failing to find a label that was
never going to appear literally.

Question IDs: `FIN-T4-02-06`, `FIN-T4-04-14`, `FIN-T4-04-15`, `FIN-T4-04-16`,
`FIN-T4-04-17`, `FIN-T4-04-18`, `FIN-RG-13-V01`

**Example — `FIN-T4-02-06`** ("For approved-scenario budgets, how many budget
periods recorded ledger activity for their account versus none?")
- expected: `has_actuals | 236; no_actuals | 251`
- actual (platform): "...236 periods recorded ledger activity for their
  associated account, while 251 periods recorded none..."
- `exact_match_detail`: `label 'has_actuals' not present; label 'no_actuals'
  not present`
- judge `factual_correctness`: 5 — "exactly matches... 236... 251"

**The problem:** not a synonym-table gap like the hyphenated-label case
below — there is no natural-language synonym for `has_actuals`. This
label never had a chance of appearing in anyone's prose, platform or
otherwise; see
[Category 1](finance_category1_internal_label_v1.0.0-full-fixed2.md) for
the general write-up.

---

## Category 2 — A hyphenated label isn't recognized (1)

`FIN-T5-04-16` — "...which three account subtypes received the most ledger
entries?" expected `AccountsPayable | 20211; RetainedEarnings | 19396;
LongTermDebt | 17540`. The platform wrote "Accounts Payable" and "Retained
Earnings" (both matched fine — the two-word CamelCase split is already
handled), but "Long-Term Debt" uses a **hyphen** where the others used a
space.

- `exact_match_detail`: `label 'LongTermDebt' not present`
- judge `factual_correctness`: 5 — "match the expected answer exactly...
  Full match."

**The problem:** this evaluation's label matching treats a space as an
inserted word-separator in a CamelCase name but doesn't treat a hyphen the
same way — a narrower, purely-mechanical case of the CamelCase-rendering
problem flagged for Logistics/Project Management elsewhere in this
repository's findings. The platform's own phrasing here is fine; the gap
is in how this evaluation reads it.

---

## Category 3 — A negative value loses its sign (1)

`FIN-T5-06-23` expects `... | -515789370.20 | -22.32 | down`. The platform
wrote: "a drop of roughly **$515,789,370.20**, or about **-22.3%**". The
percentage carries its sign; the dollar figure next to it is printed as a
bare positive magnitude, with the decrease conveyed only by the word
"drop" — never a minus sign.

- `exact_match_detail`: `value '-515789370.20' not present`
- judge `factual_correctness`: 5

**The problem:** the same answer signs one derived figure but not the
other, for the same quarter-over-quarter change. **Note:** a near-identical
case, `FIN-T5-06-22`, is NOT the same issue — there the platform never
printed a percentage figure at all (see §4), which is a missing field, not
a sign-rendering inconsistency, even though the `exact_match_detail`
message looks superficially similar.

---

## Category 4 — Platform omitted one required field, rest correct (1)

`FIN-T5-06-22` expects `1025062593.13 | 632997816.06 | -392064777.07 |
-38.25 | down`. The platform's Q1/Q2/absolute-change figures are all exactly
right (and signed correctly: "-$392,064,777"), but it never states a
percentage change anywhere in a fairly long answer — the required 5th field
is simply missing from the response, not misrendered.

- `exact_match_detail`: `value '-38.25' not present`
- judge `factual_correctness`: 4

**Resolution:** none available in this codebase. The scorer cannot pass a
value that was never stated; this is the platform's answer being incomplete
against the question's own 5-field contract.

---

## Category 5 — Reference SQL cardinality doesn't match the question's intent (2)

`FIN-T3-12-28` ("How many FX rate records have at least one matching
transaction on their rate date?") and its rephrase `FIN-RG-10-V01` both
expect **100000**. Re-running `reference_sql` against the exported dataset
reproduces 100000 exactly — so the number is *consistent with its own SQL*,
which is why this is not simply a wrong `expected_answer` typo. The problem
is the SQL itself:

```sql
SELECT COUNT(*) AS rate_count
FROM fx_rates f
INNER JOIN transactions t
    ON f.from_currency = t.source_currency
    AND f.to_currency = t.target_currency
    AND f.rate_date = t.transaction_date;
```

This counts **matched (fx_rate, transaction) pairs** — one `fx_rates` row
with same-day transactions on 50 different accounts contributes 50 rows to
the count. But the English question asks how many **FX rate records** have
at least one match — a per-row question, which this join answers only if
each FX rate row joins to at most one transaction. It doesn't: multiple
transactions commonly share a currency pair and date, so the join
multiplies. The platform's own answer, **2,444**, is internally consistent
with its sibling question `FIN-T3-11-27` (3,650 total − 1,206 unmatched =
2,444 matched) and reads as the platform correctly computing
`COUNT(DISTINCT f.rate_id)`-style semantics — i.e. the platform's
*interpretation* of the question is arguably the more faithful one.

- `exact_match_detail`: `value '100000' not present` (both IDs)
- judge `factual_correctness`: 1 for both (judged against our, questionable,
  expected answer)

**Resolution (would-be):** fix `reference_sql` for this question family in
`qa_pairs/generator/finance/` to `COUNT(DISTINCT f.<rate_id_or_pk>)` so the
query's cardinality matches the question's wording, then regenerate
`expected_answer` for `FIN-T3-12-28` and its `FIN-RG-10` rephrase group. This
is a Q&A-pair-authoring fix, not a scorer fix and not a platform fault.

---

## Category 6 — Platform ignores a WHERE filter or outer-join NULL-check (4)

Four failures share a mechanism already named in `judge/README.md`'s own
"T3 NULL handling (§9.2)" diagnostic: a `LEFT JOIN ... WHERE <child>.id IS
NULL` question (or an equivalent single-table filter) comes back answered as
if the filter/outer-join weren't applied.

Question IDs: `FIN-T3-06-19`, `FIN-T3-07-20`, `FIN-RG-07-V01`, `FIN-T3-11-27`

- `FIN-T3-06-19` ("How many reversed transactions have never been posted to
  the ledger?") — expected **132** (verified by direct DuckDB re-run of the
  reference LEFT JOIN). Platform: **0** — "every transaction... has gone
  through the posting process."
- `FIN-T3-07-20` (non-reversed version) — expected **4868** (verified).
  Platform: **0**, same reasoning.
- `FIN-RG-07-V01` ("transactions from Billing unposted") — expected **1264**.
  Platform: **0** — "All Billing system transactions have been posted."
- `FIN-T3-11-27` is a filter-ignoring variant rather than a join variant: it
  asks specifically about `rate_source = 'SyntheticEstimated'` FX rates (377
  of 3,650 total rows), expected **104** unmatched (verified). The platform's
  own stated denominator — "3,650 total synthetic-estimated records" — is
  the *total fx_rates row count*, not the synthetic-estimated subset; it
  silently dropped the `rate_source` filter and answered for the whole
  table (2,444 matched + 1,206 unmatched = 3,650).

**Resolution:** none available in this codebase — this is the platform's
query construction, not ours, and the pattern is the same one
`judge/README.md` already documented as a known platform weakness class
(it has its own diagnostic flag, `null_handling`, precisely because it's
expected to recur). Worth flagging in the cross-domain summary: this is
the single most mechanically-identical failure cluster found in Finance,
and every T3 pair in every domain is a candidate for the same weakness.

---

## Category 7 — Platform truncates a required full labelled breakdown (5)

Question IDs: `FIN-T4-07-23`, `FIN-T4-07-24`, `FIN-T4-07-25`, `FIN-T4-07-26`,
`FIN-T4-07-27` — all "break down ledger entry count by account subtype"
questions expecting all 15-17 subtype/count pairs. The platform gets the
top 5 exactly right, then switches to prose ("...with the remaining subtypes
— including X, Y, Z — contributing smaller amounts") instead of stating
every value.

- `exact_match_detail` (`FIN-T4-07-23`): `value '3359' not present; value
  '2987' not present; ... (+11 more)`
- judge `factual_correctness`: 3 — "correctly states the top 5... for the
  remaining 12 subtypes... only lists their names without [values]"

**Resolution:** none available in this codebase — the platform chose to
summarize rather than enumerate; nothing in the response contains the
missing values for the scorer to find.

---

## Category 8 — Platform materially wrong computation, verified against ground truth (13)

Every one of these was independently re-run via `reference_sql` against
`release/v1.0.0-full-fixed2/finance/dataset/*.csv` in DuckDB; every one
reproduced `expected_answer` exactly, confirming the dataset and the pair are
correct and the discrepancy is the platform's.

Question IDs: `FIN-T2-06-30`, `FIN-T2-09-35`, `FIN-T4-02-07`, `FIN-T4-02-08`,
`FIN-T4-06-21`, `FIN-T4-06-22`, `FIN-T5-02-07`, `FIN-T5-02-08`, `FIN-T5-02-10`,
`FIN-T5-06-21`, `FIN-T5-06-24`, `FIN-RG-06-V01`, `FIN-RG-06-V02`

- **`FIN-T2-06-30`** ("total USD amount for USD transactions with known FX
  rate") — expected **2,345,393,479.41** (verified exactly). Platform:
  **≈$19.76 billion**, 8.4x too high — almost certainly summing across all
  source currencies rather than filtering to `source_currency = 'USD'`.
- **`FIN-T2-09-35`** ("total budgeted amount for accounts payable accounts")
  — expected **2,987,315.56** (verified exactly). Platform: "No budgeted
  amount data was found... null total" — it failed to match the
  `AccountsPayable` account subtype filter at all.
- **`FIN-T4-02-07`/`08`, `FIN-T4-06-21`/`22`** — four "budget periods with vs.
  without ledger activity" variants (same family as §1) where, unlike §1,
  the platform's *numbers themselves* are wrong, not just mislabeled — e.g.
  `FIN-T4-06-21` expects 182/207, platform reports "zero budget periods
  overall... no basis to report."
- **`FIN-T5-02-07`, `FIN-T5-02-08`, `FIN-T5-02-10`, `FIN-T5-06-21`,
  `FIN-T5-06-24`, `FIN-RG-06-V01`, `FIN-RG-06-V02`** — FX-adjusted
  quarter-over-quarter comparisons (the same question family as the verified
  example below) where the platform's Q1/Q2 totals differ from ground truth
  by tens to hundreds of millions — not a rounding gap, a different
  computation (most likely a different FX-rate join or a different account
  filter than the reference SQL's `(from_currency, to_currency, rate_date)`
  match).

**Verification example (`FIN-T5-02-07`):** reference SQL re-executed against
the exported dataset returns exactly `(1303037797.69, 919442704.90,
-383595092.78, -29.44, 'down')` — identical to `expected_answer` to the
cent. The platform reported Q1 ≈ $1,343,243,119 / Q2 ≈ $919,450,618, a Q1
discrepancy of ~$40M that cannot be rounding. No duplicate `fx_rates` keys
exist on `(from_currency, to_currency, rate_date)` in the exported dataset
(checked directly), ruling out a join-multiplication explanation on the
reference side — the dataset and pair are clean; the platform's own FX
computation is what's wrong.

**Resolution:** none available in this codebase. These are platform
(Pulse) computation errors, most concentrated in FX-adjusted quarter
comparisons and account-activity-presence questions — worth flagging to
the platform owner as a cluster, since 10 of the 13 share the FX-conversion
or budget-activity-join shape.

---

## Category 9 — One-cent rounding cascade on a derived delta (1)

`FIN-T5-02-06` — Q1/Q2 absolute figures and the percentage all match
exactly; only the absolute change fails: expected `-798881494.76`, platform
states "$798,881,494.**77**" — one cent off, because the platform most
likely computed `Q2 - Q1` from its own unrounded intermediate sums while the
reference computes it from the two already-rounded quarterly totals (or
vice versa). judge `factual_correctness`: 5, calling it "a rounding
artifact."

**Resolution:** not a scorer bug to fix — §HC-3 and this project's own
settled position (`judge/README.md`'s OI-2) are that numeric comparison is
zero-tolerance by design; relaxing it to absorb this would also absorb real
one-cent errors elsewhere. Not a dataset defect either (the reference value
is internally consistent). This is an inherent artifact of two independent
systems rounding a derived value at different points in the calculation,
and is believed to be a residual-risk case rather than a bug anywhere.

---

## Summary

| # | Category | Count |
|---|---|---|
| 1 | Internal field name used as a record label (`has_actuals`/`no_actuals`) | 7 |
| 2 | Hyphenated label not recognized (`LongTermDebt`) | 1 |
| 3 | Negative value loses its sign | 1 |
| 4 | Platform omitted one required field, rest correct | 1 |
| 5 | Reference SQL cardinality mismatch vs. question intent | 2 |
| 6 | Platform ignores WHERE filter / outer-join NULL-check | 4 |
| 7 | Platform truncates a required full labelled breakdown | 5 |
| 8 | Platform materially wrong computation (ground-truth verified) | 13 |
| 9 | One-cent rounding cascade on a derived delta | 1 |
| | **Total** | **35** |

Categories 4, 6, 7, and 8 (23 failures) are the live platform computing or
reporting a materially different answer than the ground truth. Categories
1–3 (9 failures) are mismatches between the platform's own phrasing and
this evaluation's expectations — the platform's numbers are independently
confirmed correct in every one of them by the judge. Category 5 (2
failures) is a flaw in this project's own reference query, not the
platform's. Category 9 (1 failure) is a one-cent rounding artifact from
two systems deriving the same value at different points in the
calculation — not attributable to either side as a defect.

Even setting categories 1–3, 5, and 9 aside entirely, 23 of 35 failures
remain concentrated in FX-adjusted quarter comparisons and
account-ledger-activity presence questions (category 8), plus the
documented T3 NULL-handling weakness (category 6) that is very likely to
recur in every domain's T3 tier, not just Finance's.
