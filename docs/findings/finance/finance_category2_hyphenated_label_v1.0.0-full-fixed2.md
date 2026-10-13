# Finance — Category 2: A Hyphenated Label Isn't Recognized (1 question)

A ranking question's expected labels are compact, no-space names
(`AccountsPayable`, `RetainedEarnings`, `LongTermDebt`). The answer spelled
two of them out with spaces ("Accounts Payable," "Retained Earnings") and
those were read correctly. The third, "Long-Term Debt," uses a hyphen
instead of a space.

## Example

**Question:** "...which three account subtypes received the most ledger
entries?"
**Expected:** `AccountsPayable | 20211; RetainedEarnings | 19396;
LongTermDebt | 17540`
**Platform's wording:** "...Accounts Payable... Retained Earnings...
Long-Term Debt..." — all three numbers correct, all three names
recognizably spelled out.

## The problem

Space-separated and hyphen-separated versions of the same compact name
aren't treated the same way when checking whether the answer named the
right thing. The platform's phrasing here isn't wrong by any normal
reading — "Long-Term Debt" is a perfectly natural way to write that term.
Noted for completeness; this is the same shape of issue flagged for
Logistics and Project Management elsewhere in these findings.
