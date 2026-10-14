# "non_billable" Not Recognized Inside a Longer Response (11 questions — Project Management only, known hard limitation)

11 questions involving a billable/non-billable breakdown fail even
though both numbers are stated correctly and attached to the right
label in plain English.

## What's happening

Example: "For resources in the delivery department, how does the count
of logged time entries break down by billable status?" Expected
`billable | 20183; non_billable | 5684`. Answer: "...20,183 were marked
billable and 5,684 were marked non-billable..." — both numbers correct,
both correctly paired with their label, in natural phrasing.

The underlying difficulty: `non-billable` contains the word `billable`
as a substring, and the word `billable` also appears elsewhere in the
same response on its own (totals, other breakdowns). Telling which
occurrence of "billable" is part of "non-billable" and which is a
free-standing mention — reliably, across arbitrary response lengths and
structures — is a hard problem in general, not specific to one phrasing
choice. One instance (`PM-RG-08-V01`) shows even the plain `billable`
label losing its pairing in a longer response, confirming this isn't
limited to the non-billable/billable overlap specifically — long,
multi-section responses make this kind of label-to-value pairing harder
generally.

All 11 instances: `PM-T4-04-14`, `PM-T4-04-15`, `PM-T4-04-16`,
`PM-T4-04-17`, `PM-T4-04-18`, `PM-T4-07-28`, `PM-T4-07-29`,
`PM-T4-07-30`, `PM-T4-07-31`, `PM-T4-07-32`, `PM-RG-08-V01`.

## Status

This is an **open, known, and deliberately unfixed** limitation of how
this evaluation pairs a label with its value in free-form text — not a
case where the underlying answer is wrong. Every instance here is
independently confirmed correct by the judge. No reliable fix has been
found: approaches tried so far either miss this case or risk breaking
other, currently-passing label pairings elsewhere (the same proximity
and ownership checks that catch this case correctly also protect
against a value being borrowed from the wrong label in long answers —
loosening them generally would trade this known failure for new,
harder-to-detect false passes). Documented here as a known limitation
rather than a fix in progress.
