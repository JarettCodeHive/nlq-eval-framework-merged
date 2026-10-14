# Required List/Breakdown Truncated to Top-N (7 questions — CRM 2, Finance 5)

The platform correctly enumerates the top handful of a ranked or
labelled breakdown with exact, correct numbers, then switches to prose
for the remainder instead of listing the rest of what the question
asked for.

## CRM (2)

"For [campaign type] campaigns, which contact titles carry the most
total multi-touch attribution weight?" — the correct answer has 7
ranked titles with a number each. `CRM-T4-04-16` and `CRM-T4-04-17` both
correctly name and rank 5 of the 7 titles with the right numbers, then
switch to prose for the rest ("...also present at similar levels")
instead of giving numbers for the last two.

## Finance (5)

"Break down ledger entry count by account subtype." Expected: all
15–17 subtype/count pairs. The answer gives the top 5 with exact
numbers, then: "...with the remaining subtypes — including X, Y, Z —
contributing smaller amounts" — no numbers for the rest.

Affected: `FIN-T4-07-23`, `FIN-T4-07-24`, `FIN-T4-07-25`, `FIN-T4-07-26`,
`FIN-T4-07-27`.

## Status

Not fixable in this evaluation — the numbers given are correct, and
nothing in the response contains the missing values for the scorer to
find. When a question asks for "which X" without a stated limit, or to
"break down by" a dimension, the platform should enumerate every item
in the real ranking/breakdown (or state the limit explicitly), rather
than defaulting to a shorter list and summarizing the remainder in
prose.
