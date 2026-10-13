# Finance — Category 1: An Internal Field Name Used as a Record Label (7 questions)

7 questions about budget periods "with vs. without ledger activity" use
`has_actuals` and `no_actuals` as the labels in the expected answer. Those
are internal field names from this evaluation's own data generator — not
words that would ever appear in a natural-language answer, from a
platform or a person.

## Example

**Question:** "For approved-scenario budgets, how many budget periods
recorded ledger activity for their account versus none?"
**Expected label pair:** `has_actuals | 236; no_actuals | 251`
**Platform's actual wording:** "...236 periods recorded ledger activity
for their associated account, while 251 periods recorded none..."

The numbers (236 and 251) are exactly right. The response just never says
the literal words `has_actuals` or `no_actuals` — because there's no
reason it ever would.

## The problem

This is a mismatch inside the test itself, not something to point out
about an external answer: the expected-answer label and the only way
anyone would actually phrase that answer were never going to be the same
string. Noting it here for completeness and traceability.
