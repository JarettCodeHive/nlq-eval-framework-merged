# Project Management — Category 4: Trend Answers Omit the Percentage Change (11 questions)

11 quarter-over-quarter trend questions expect five figures — Q1 total,
Q2 total, absolute change, percentage change, and a direction word — and
the percentage is consistently the one figure missing.

## What's happening

**Example:** Expected `11886.49 | 15966.20 | 4079.71 | 34.32 | up`. Answer:
"...rose from 11,886.49 hours in Q1 2026... to 15,966.20 hours in Q2
2026..., an increase of 4,079.71 hours..." — Q1, Q2, the absolute change,
and the direction ("rose") are all there. The percentage, `34.32`, is
never computed or stated anywhere in the response.

This is consistent across all 11 instances: the first four parts of the
answer are reliably present, the percentage is reliably the one left out.

## A separate, unrelated issue found while reviewing this category

One instance (`PM-RG-16-V01`) produces a diagnostic message that reads
"...present but **None** chars from any of its values..." instead of a
number. This is a real defect in how that message is built
(`judge/exact_match.py` around line 900–904): when the distance between a
label and its value can't be computed for a particular kind of label, the
code still tries to print that distance, and gets the literal string
`None` instead of a number or a clearer explanation. It doesn't change
whether this question's answer passes or fails (it fails anyway, for the
same missing-percentage reason as every other row in this category) — it
only makes the diagnostic output confusing when someone reads it.
