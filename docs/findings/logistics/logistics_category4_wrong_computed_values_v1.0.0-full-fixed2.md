# Logistics — Category 4: Materially Wrong Computed Values (1 question)

One quarter-over-quarter comparison returns numbers that are not just off
by a rounding margin, but structurally wrong — the two periods appear
swapped, and one of them is reported as zero.

## Example

**Question:** a rephrase asking to compare total order value for an
APAC warehouse across two consecutive quarters.
**Expected:** `79421787.31 | 18610.50 | -79403176.81 | -99.98 | down` (a
drop from $79.4M to $18.6K).
**Answer:** reports the earlier quarter as **$0.00** and the later one as
$18,610.50 — the two periods come back misaligned and one of them zeroed
out, rather than $79.4M → $18.6K.

The two other variants of this same underlying question (differently
phrased, same ground truth) also disagree with each other on the result —
not just with the expected answer — which independently confirms the
inconsistency sits in how the comparison was computed for this phrasing,
not in the expected answer itself.

## The problem

For this specific question's phrasing, the quarter-over-quarter
comparison comes back with one period zeroed and the periods apparently
misaligned, rather than reflecting the real $79.4M → $18.6K drop.
