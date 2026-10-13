# Finance — Category 3: A Negative Value Loses Its Sign (1 question)

A quarter-over-quarter change question expects a signed dollar amount, a
signed percentage, and a direction word together (e.g.
`-515789370.20 | -22.32 | down`). The answer got the direction word and
the signed percentage right, but printed the dollar figure as a plain
positive number.

## Example

**Expected:** `... | -515789370.20 | -22.32 | down`
**Platform's wording:** "a drop of roughly $515,789,370.20, or about
-22.3%"

The percentage keeps its minus sign; the dollar amount right next to it
drops the sign and relies on the word "drop" instead.

## The problem

Within the same answer, one of two parallel figures describing the same
decrease is signed and the other isn't — an internal inconsistency in how
the two numbers for one change are presented, independent of whether the
underlying computation is correct (it is).
