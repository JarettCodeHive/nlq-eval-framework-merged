# NULL Rows Counted Together With Orphan Rows (1 question — Logistics only)

`LOG-T3-09-30` — "How many cancelled orders reference a `warehouse_id`
that does not match any real warehouse record?" Expected `101`. The
answer gives `370`, explicitly breaking it down as "269 orders have no
warehouse identifier at all (a missing or blank value)" plus the
remainder genuinely orphaned.

## What's happening

The query behind this answer uses a "not found" style check which, per
standard SQL `NULL` comparison semantics, also matches rows where
`warehouse_id` is `NULL` — folding the dataset's deliberately separate
**NULL-`warehouse_id`** imperfection into the **orphan-`warehouse_id`**
imperfection the question is actually asking about (see
`schemas/logistics/logistics_semantics.md`). The expected answer
isolates true orphans only; the response's query conflates the two
distinct imperfection categories.

This is the same mechanism, on a different sibling question, as a
closely related question in this same family (`LOG-T3-09-32`, the
shipped-orders variant) — that row passed in this run instead, with the
platform correctly separating NULL from orphan that time. The
underlying defect recurs; which specific sibling question trips it
varies run to run.

## Status

Not fixable in this evaluation. The fix is on the platform's query-
generation side: a true "orphan" check needs `warehouse_id IS NOT NULL
AND NOT EXISTS (...)`, not a bare `NOT EXISTS (...)` that also catches
NULLs by SQL's own three-valued-logic semantics.
