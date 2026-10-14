# No-Data Result Where Real Data Exists (1 question — Logistics only)

`LOG-T4-06-21` — "For finished goods inventory, how does total quantity
on hand break down by warehouse region?" Expected `LATAM | 15982400; EU |
11560637; APAC | 11380724; NA | 8952671`. The response states "No data
was found... a single result with a null value... no breakdown by
warehouse region was available." Judge `factual_correctness: 1`.

## What's happening

The data clearly exists — a rephrase of this same question resolved a
full four-region breakdown successfully in a prior analysis, and a
related finished-goods question elsewhere in this run answers
correctly. The failure is tied to this exact phrasing producing an
empty query result on the platform's side, not to any gap in the
dataset. This isn't the same mechanism as the categorical-literal
mismatch category elsewhere in this index — there's no evidence of a
spaced-vs-compact filter literal here, just an empty result from this
specific phrasing.

## Status

Not fixable in this evaluation. The platform's own query construction
for this exact question returns nothing, despite the data existing and
a close variant of the same question succeeding.
