# CRM — Category 2: SLA Compliance Rate Lands Close But Wrong (5 questions)

This explains why 5 "SLA compliance rate" questions got an answer that
looks right (off by well under 1 percentage point) but is actually
computed from the wrong group of cases.

## What's happening

A support case counts as "resolved" in two different ways depending on
which column you check:

- Its **status** column says `Resolved`.
- It has a **`resolved_at`** timestamp filled in.

In this dataset, both `Resolved`-status cases *and* `Closed`-status cases
carry a `resolved_at` timestamp — `Closed` is a second, equally valid way a
case finishes. Your SQL filters by `status ILIKE '%resolved%'`, which only
keeps the `Resolved`-status cases and silently drops every `Closed` one —
about **a third of all actually-resolved cases** — even though they have a
real resolution date and a real SLA outcome.

## Example

**Question:** "Of support cases for education accounts that have been
resolved, what percentage were resolved within SLA?"
**Correct group:** every case with `resolved_at` filled in → 9,485 cases → **49.87%**
**Your group:** only cases with `status = 'Resolved'` → 6,181 cases → **50.11%**

The two percentages land close together by coincidence (the excluded
`Closed` cases aren't meaningfully different from the `Resolved` ones), but
the case counts behind them differ by ~35%.

## Where this showed up

The same `status`-vs-`resolved_at` mismatch repeats for every industry
checked, each with its own case counts but the same ~35% denominator gap:

| Question | Industry | Correct (resolved_at-based) | Your answer (status-based) |
|---|---|---|---|
| Support SLA rate | Education | 9,485 cases → **49.87%** | 6,181 cases → **50.11%** |
| Support SLA rate | Energy | 9,568 cases → **50.11%** | 6,294 cases → **49.98%** |
| Support SLA rate | Manufacturing | 9,318 cases → **49.74%** | 6,022 cases → **49.30%** |
| Support SLA rate | Retail | 9,217 cases → **49.76%** | 5,903 cases → **48.89%** |

One related question in this family went further wrong, in a different
way: a rephrased version of the Education question (asking for the "share
of support cases that met the SLA deadline") dropped the "resolved" filter
from the denominator entirely and divided by *every* support case
regardless of status — 20,376 cases instead of 9,485 — landing at **23.21%**
instead of ~50%. Same underlying confusion about what counts as
"resolved," taken one step further.

## Suggested fix

Treat `resolved_at IS NOT NULL` (a case has a resolution date) as the
definition of "resolved," not the `status` label — a case can finish as
either `Resolved` or `Closed` and both should count. If `status` needs to
stay the primary signal for other purposes, at minimum include both
`Resolved` and `Closed` in this kind of filter rather than only `Resolved`,
and make sure the denominator for a "resolved" question is never the full,
unfiltered table.
