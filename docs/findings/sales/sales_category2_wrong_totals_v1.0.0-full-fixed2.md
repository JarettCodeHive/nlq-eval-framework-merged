# Sales — Category 2: Materially Wrong Totals (18 questions)

The largest Sales group. Five distinct, confirmed causes, each verified
by comparing the generated SQL against the reference SQL and the exported
dataset directly.

## Cause 1 — a date range written as inclusive-both-ends instead of start-inclusive/end-exclusive (7 questions)

Every quarter/target-period window in the reference data is defined as
`[period_start, period_end)` — the start date counts, the end date
doesn't, because the end date is the *next* period's start. Several
generated queries instead use `BETWEEN period_start AND period_end`,
which includes both ends. A deal that closes exactly on a period boundary
date gets pulled into the wrong (or an extra) period.

Example: "How many Won deals closed inside their rep's sales target
period?" — expected `940`, answered `945`. Same mechanism in the
West-territory "zero-won-amount target periods" count (expected `24`,
answered `23` — one boundary-exact deal now counts as "won" for a period
the reference treats as empty) and the Central-territory won-amount
breakdown by target period (right for most periods, off for the ones with
a boundary-exact close date).

Affected: `SALES-T2-09-37`, `SALES-T3-03-15`, `SALES-T4-04-14`,
`SALES-T4-04-15`, `SALES-T4-04-16`, `SALES-T4-04-17`, `SALES-T4-04-18`.

## Cause 2 — quarter window applied to the wrong date column (6 questions)

Six "quoted revenue by product category" trend questions filter the Q1/Q2
window on `quotations.quoted_at` (when the quote was written). The
reference filters the same window on `deals.close_date` (when the deal
actually closed). A quote and its deal's close date are often weeks apart,
so every quarterly total built this way comes out consistently lower than
the correct one — e.g. hardware deals: `$878.4M` vs. expected `$984.0M` for
the same quarter.

Affected: `SALES-T5-05-19`, `SALES-T5-05-20`, `SALES-T5-05-21`,
`SALES-T5-05-22`, `SALES-T5-06-23`, `SALES-RG-14-V01`.

## Cause 3 — joining through a non-unique name column multiplies every row (1 question)

A North-territory Q1→Q2 won-deal comparison joins `deals` to `leads` on
`rep_name` (a plain text name, not an id) to find each deal's territory.
50 reps share 15,000 leads between them — about 300 lead rows per rep —
so this join turns one deal row into roughly 300 duplicate rows before
summing. Expected Q1 total: `$6.75M`; answered: `~$2.05 billion` — almost
exactly 300x too large, matching the average leads-per-rep ratio.

Affected: `SALES-T5-03-12`.

## Cause 4 — a different definition of "most recently completed period" (3 questions)

The reference defines "the most recently completed target period" as a
single global period: the one with the latest `period_end` across every
target row in the data, no territory distinction. The generated query
instead computes the latest period **per rep**, keeping only periods whose
end date is on or before the real-world current date — a different, and
narrower, selection rule that can land on a different period for different
reps.

Affected: `SALES-T5-02-06`, `SALES-T5-02-08`, `SALES-T5-02-09`.

## Cause 5 — a status word in the question gets dropped from the filter (1 question)

"How many **new** leads have never become a deal?" The reference filters
to `lead_status = 'New'` before counting unconverted leads (`2228`). The
generated query drops that status filter and counts every unconverted
lead regardless of status (`10000` — the full unconverted-lead population
for every status combined, not just `New`).

Affected: `SALES-T3-02-09`.
