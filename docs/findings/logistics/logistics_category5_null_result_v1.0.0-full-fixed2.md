# Logistics — Category 5: No-Data Result Where Real Data Exists (1 question)

One breakdown question returns "no data was found" even though the
breakdown exists and is correctly produced for a differently-phrased
version of the same underlying question.

## Example

**Question:** "For finished goods inventory, how does total quantity on
hand break down by warehouse region?"
**Expected:** `LATAM | 15982400; EU | 11560637; APAC | 11380724; NA |
8952671`
**Answer:** "No data was found... a single result with a null value... no
breakdown by warehouse region was available."

A rephrase of this same question, asked separately, correctly returns the
full four-region breakdown — the data clearly exists and is reachable; it
just wasn't reached for this exact phrasing.

## The problem

For this specific wording of the question, the query that's built returns
nothing, even though a different phrasing of the identical question
returns the correct breakdown. The failure is tied to how this particular
phrasing gets turned into a query, not to data availability.
