# Logistics — Category 2: A Change Value Loses Its Sign (1 question)

In the same Q1-vs-Q2 trend question family as Category 1, one answer
states the percentage change correctly, signed, but states the absolute
dollar change as an unsigned, rounded magnitude instead of the exact
signed figure.

## Example

**Question:** air-carrier shipping cost, Q1 2026 → Q2 2026.
**Expected:** `59449126.42 | 2345667.61 | -57103458.81 | -96.05 | down`
**Answer:** "...dropping from about $59,449,126 in Q1 to about $2,345,668
in Q2 — a drop of roughly $57,103,459, or about -96.05%."

The percentage (`-96.05%`) is stated explicitly with its sign, and the
Q1/Q2 values round-trip correctly. Only the dollar change is printed as a
plain, rounded, positive number (`$57,103,459`) instead of the exact
signed value (`-57103458.81`).

## The problem

Within the same answer, one of two parallel figures describing the same
decrease carries its sign and is stated to the cent, and the other is
rounded to a whole-dollar, unsigned magnitude — an inconsistency in how
precisely and how completely two figures from the same calculation get
presented.
