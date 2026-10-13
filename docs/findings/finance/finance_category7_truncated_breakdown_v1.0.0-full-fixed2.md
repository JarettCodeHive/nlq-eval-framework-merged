# Finance — Category 7: Breakdown Cut Short at Top 5 (5 questions)

5 questions ask for a full breakdown
across every account subtype (15–17 of them) and only get the top 5, with
the rest summarized in prose instead of listed.

## What's happening

**Question:** "Break down ledger entry count by account subtype."
Expected: all 15–17 subtype/count pairs. Your answer correctly gives the
top 5 with exact numbers, then switches to: *"...with the remaining
subtypes — including X, Y, Z — contributing smaller amounts"* — no numbers
for the rest.

The top 5 that are given are correct. The response just doesn't go all
the way through the full list the question asked for.

## Suggested fix

Same shape as a similar finding in the CRM analysis: when a question asks
to "break down by" a dimension, return every value that dimension has in
the data, not a top-N cutoff with a prose summary for the remainder. If
there's an internal row-limit on breakdown results, either raise it for
this kind of question or state explicitly how many categories exist beyond
what's shown.
