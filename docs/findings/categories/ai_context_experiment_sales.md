# Experiment: "AI generated data context" — Sales domain

Not a failure category — a comparison of two full Sales runs, one with a
per-table context block added in the application, one without, everything
else identical (same dataset, same Q&A pairs, same `v1.0.0-fixed` codebase).
Written up here because it answers a question several of the categories
above ask explicitly: can the platform's own context feature close any of
these without a code or dataset change?

**Without context:** `release/v1.0.0-fixed/sales/judge/20261013T102604Z/results.json`
**With context:** `release/v1.0.0-ai-context/sales/judge/20261019T115650Z/results.json`

## The context that was added

```
leads.csv
lead_source is a compact CamelCase token with no spaces — filter on
the exact stored value: ColdCall, EventBooth, Inbound, Referral, WebForm.

quotations.csv
quote_status is case-sensitive — filter on the exact stored value:
Accepted, Draft, Expired, Rejected, Sent. All five are valid; include
all of them when a question asks for a group like "decided" quotations.

targets.csv
Target/quota periods are half-open: period_start <= date < period_end.
The end date belongs to the next period — do not use BETWEEN.

"Most recently completed period" means one single period shared across
all sales representatives (the latest period_end in this table), not a
separate period computed per representative.

deals.csv
Join sales representatives by their unique identifier, not by name —
rep names are not guaranteed unique and joining on name text can
multiply rows.
```

## Headline result

| | Eligible | Pass | Fail | Pass % |
|---|---|---|---|---|
| Without context | 179 | 134 | 45 | 74.86% |
| With context | 179 | 154 | 25 | 85.96% |

*One question moved to a clarification (excluded from both sides of the
percentage) instead of either passing or failing.

Net: **+19 passing**, a jump of **+11.1 points**. Judge `mean_overall` also
rose, 4.20 → 4.50. But the gain isn't a clean "+19 fixed, 0 broken" — it's
25 questions fixed and 6 questions that were passing before now fail. Both
halves matter for deciding whether to roll this out further.

## What the context fixed (25 questions)

All but one of these fall squarely inside categories already documented
above — this experiment is confirmation that those documented mechanisms
really are what the context describes, not a new finding on its own.

**`categorical_literal_mismatch` — 20 of 20 Sales members fixed, 100%.**
Every single Sales question in that category (`SALES-T1-03-10`,
`-11`, `-14`, `T1-06-22`, `-23`, `-26`, `T2-01-05`, `T2-08-33`, `T2-10-38`,
`T2-10-40`, `T3-05-28`, `T3-05-32`, `T4-03-09`, `-10`, `-13`, `T4-06-23`,
`-27`, `RG-02-V01`, `RG-07-V01`, `RG-15-V01`) now passes. Telling the
platform the literal stored tokens for `lead_source` directly closed the
single largest fixable-shaped pattern in Sales, with zero collateral
damage anywhere else in the run.

**`breakdown_computation_mismatch` — 4 of 19 Sales members fixed (21%).**
Specifically the half-open-date-range mechanism (`SALES-T2-09-37`: 945→940,
`SALES-T3-03-15`: 23→24) and the incomplete-status-enumeration mechanism
(`SALES-RG-03-V01`: 50.68%→34.19%, same fix also closes `SALES-T2-08-33`,
already counted above since it's shared with the literal-mismatch list).
One member of the "most recently completed period" mechanism
(`SALES-T5-02-05`) also flipped to pass — but see "What it broke" below,
because this one is not the clean win it looks like in isolation.

**One previously undocumented case (`SALES-T2-05-21`).** Before context,
the platform's quote-status breakdown had the right five numbers in the
wrong order relative to their labels (`exact_match_detail`: "label
'Rejected' present but 178 chars from any of its values... a different
label claims its value"). With context, the same five numbers come back
attached to the correct labels. This is a label-ownership/attachment-
distance case, not one of the ten surviving categories — it was previously
counted under a category this project deleted as out of scope (label
proximity). Worth noting that the *exact same* mechanism also appears in
"what it broke" below, in the opposite direction.

## What the context broke (6 questions that were passing before)

This is the part worth being careful about before recommending this
context be rolled out everywhere — it is not purely additive.

**Two instances confirm a previously-dormant mechanism is real.**
`breakdown_computation_mismatch`'s Sales write-up already named a sixth
mechanism — "a status word dropped from the filter, e.g. **new** leads
never became a deal drops `lead_status = 'New'`" — and noted "this
mechanism's only member passed in the current run — kept here as a
description, not currently observed failing." With context added,
`SALES-T3-02-09` ("How many **new** leads have never become a deal?",
expected 2228, answered 10,000 — every lead, not just new ones) and its
territory-scoped sibling `SALES-T3-04-24` (expected 485, answered 2,177)
both now exhibit exactly that dormant mechanism. The context didn't cause
this — the underlying SQL mistake already existed and was already
documented as plausible — but it's the first time it's actually been
observed failing in a run.

**Two instances show the "shared period" rule did not reliably take.**
`SALES-RG-12-V01` and `SALES-T5-02-05` ask the identical question (same
expected answer: `Michael Barrett | 663045.50; Autumn Simmons |
292574.37; ...` for Central territory) in different phrasing. Without
context, `RG-12-V01` passed and `T5-02-05` failed. With context,
`T5-02-05` now passes — but `RG-12-V01` now fails, with a *different*
wrong rep count (8 reps missing quota vs. the expected 6 reps' worth of
data). The same swap happens one territory over: `SALES-T5-02-07` (North)
flips from passing to failing while `SALES-T5-02-05` is the one that
improves. Net effect on this specific rule, counting all five
near-identical per-territory siblings (`T5-02-05` through `-09` plus
`RG-12-V01`): **4 failing before, 4 failing after — just not the same
four.** The context didn't make the platform apply "one shared period"
more reliably; it changed which instances happen to land on the right
side of whatever's actually driving the inconsistency.

**One instance is the label-ownership case in reverse.** `SALES-T4-02-08`
("quotation-line counts by deal stage, subscription products") passed
before context (`8 requirement(s) satisfied`) and fails with it — labels
present, but 240 characters from their values, with "a different label
claims its value." This is the mirror image of the `SALES-T2-05-21` fix
above: the same attachment-distance mechanism, flipping in both
directions depending on exactly how the platform phrases the surrounding
prose. Net effect across the two instances found in this run: zero.

**One instance lost a percentage it previously stated.**
`SALES-T5-03-13` ("South-territory won-deal amount, Q1→Q2 2026") now
omits the `35.40` percentage figure it stated before. This is the same
shape as the `trend_percentage_omitted` category's five documented Sales
members (none of which this context touches, see below) — just a sixth
instance appearing for the first time in this run.

## What the context didn't touch at all

- **`trend_percentage_omitted` — 0 of 5 fixed.** None of the five context
  paragraphs says anything about stating a percentage change, so this is
  expected, not a surprise. One of the five (`SALES-RG-13-V02`) changed
  from a flat fail to a clarification request instead — a different
  platform behavior, still not a pass.
- **15 of 19 `breakdown_computation_mismatch` members**, including every
  quarter-window-on-the-wrong-column case (`SALES-T4-04-14` through `-18`,
  `SALES-T5-05-19` through `-22`, `SALES-T5-06-23`, `SALES-RG-14-V01`) and
  the rep-name-join-multiplication case (`SALES-T5-03-12`) — the
  `deals.csv` hint about joining by id, not name, was aimed squarely at
  this last one and did not fix it.

## Recommendation

1. **The literal-token hint is the clear, unambiguous win.** 100% of its
   target category closed, zero regressions attributable to it. The same
   technique — stating the exact stored value for every categorical
   column — is worth trying against `categorical_literal_mismatch`'s other
   29 questions in CRM, Finance, and Project Management, where the
   mechanism is identical.
2. **The half-open-date-range hint also worked cleanly** on the two
   questions it could reach (`SALES-T2-09-37`, `SALES-T3-03-15`), no
   regressions. Worth extending to any other date-boundary question shaped
   like these.
3. **The "shared period" and "join by id" hints did not demonstrate a
   reliable fix** — one reshuffled which instances fail without reducing
   the count, the other didn't move its target question at all. Before
   crediting either as working, they'd need a sharper, example-based
   phrasing (e.g. naming the actual latest `period_end` value) and a
   second run to separate a real fix from the platform's already-
   documented run-to-run variance.
4. **Treat any single before/after comparison like this one as a single
   data point, not a verdict** — six questions that were passing broke,
   including two that are plausibly just this platform's known
   non-determinism (`RG-12-V01`/`T5-02-05` share one expected answer and
   landed on opposite sides of pass/fail). Re-running the same comparison
   again, with and without context, before deciding this context block is
   "safe," would separate a real regression from noise.

## Where to look these up

| | Path |
|---|---|
| Without context | `release/v1.0.0-fixed/sales/judge/20261013T102604Z/results.json` |
| With context | `release/v1.0.0-ai-context/sales/judge/20261019T115650Z/results.json` |
| Question text + expected answers | `release/v1.0.0-fixed/sales/qa_pairs/sales_qa_pairs.csv` |
