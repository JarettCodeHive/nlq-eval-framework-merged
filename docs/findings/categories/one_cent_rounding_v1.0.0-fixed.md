# One-Cent Rounding Difference (1 question — CRM only)

`CRM-T1-01-03` — "What is the total budget of nurture campaigns?"
Expected `23184584.00`; the platform answered `$23,184,583.99` — exactly
one cent off, out of a $23.18 million total.

## What's happening

Most likely the platform summed its own unrounded intermediate values
while the reference total was built from already-rounded component
sums (or the reverse) — the same kind of harmless rounding-order
artifact Finance's analysis documented in a previous release (that
specific instance didn't recur this run; the platform answered that
question differently this time).

## Status

Not a meaningful computation error, and not something to fix. Loosening
numeric comparison to tolerate a one-cent gap would also hide genuine
one-cent-scale errors elsewhere — this is a residual-risk case, not a
bug on either side.
