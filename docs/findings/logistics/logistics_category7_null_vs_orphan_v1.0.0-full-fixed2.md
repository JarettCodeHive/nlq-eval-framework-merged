# Logistics — Category 7: NULL Rows Counted Together With Orphan Rows (1 question)

This dataset deliberately distinguishes two different kinds of bad
`warehouse_id` reference: a **missing** reference (the field is blank/NULL)
and an **orphan** reference (the field is filled in, but points to a
warehouse that doesn't exist). One question asking specifically about
orphans gets an answer that counts both kinds together.

## Example

**Question:** "How many shipped orders reference a `warehouse_id` that
does not match any real warehouse record?"
**Expected:** `160` (true orphans only).
**Answer:** `678`, explicitly broken down as "518 orders have no
warehouse ID at all (a missing/blank reference)" plus the genuinely
orphaned remainder.

The query behind this answer uses a "not found in the warehouse table"
style check, which — under standard database `NULL` comparison rules —
also matches rows where the field is blank, not just rows where it holds
a value that fails to match. That folds the blank/missing case into the
same bucket as the orphan case the question specifically asked about.

## The problem

A "does not match any real record" check that doesn't separately exclude
blank/NULL values will count every blank value as a non-match too,
combining two data-quality categories (missing vs. orphaned) that this
dataset treats as distinct and that the question specifically asked
about one of.
