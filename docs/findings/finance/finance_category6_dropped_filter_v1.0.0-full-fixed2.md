# Finance — Category 6: A Stated Filter Gets Dropped (4 questions)

4 questions ask for a specific,
narrowed-down subset of transactions or FX rates, and get back an answer
for a bigger group than what was asked.

## What's happening

Each of these questions includes a scoping condition — "reversed
transactions," "transactions from Billing," "SyntheticEstimated FX rates"
— that your query either doesn't apply, or applies to the wrong half of a
join (e.g. an outer join where unmatched rows should still count as "no
match found," but get treated as if the match condition didn't exist).

## Examples

- **"How many reversed transactions have never been posted to the
  ledger?"** Expected `132`. Your answer: `0` — "every transaction has gone
  through the posting process." There are 132 such transactions; the query
  that was meant to find unposted ones returned none.
- **"...from Billing, unposted?"** Expected `1264`. Your answer: `0` — "All
  Billing system transactions have been posted."
- **A question scoped to `SyntheticEstimated` FX rates specifically**
  stated its own denominator as "3,650 total synthetic-estimated records"
  — but 3,650 is actually the total row count for the *entire* `fx_rates`
  table, not just the `SyntheticEstimated` subset (377 rows). The
  `rate_source` filter was silently dropped somewhere in the query.

## Suggested fix

When a question says "have never been X" or "that are Y," that's asking
for the *complement* of a join (rows with no match, or rows restricted to
one specific label) — double check that the generated query actually
keeps the LEFT JOIN / NULL-check / WHERE condition that implements that
restriction, rather than defaulting to a plain join or the full table.
