# Sales — Category 1: Categorical Value Stored Compactly, Filter Written With Spaces (17 questions)

Several Sales categorical columns are stored as compact tokens or with a
specific casing: `lead_source` as `ColdCall`/`EventBooth`/`WebForm`, and
`quotations.quote_status` as `Accepted` (capitalized). The generated SQL
for these questions instead filters on a human-rendered guess — a spaced
lowercase form such as `'cold call'` or `'accepted'` lowercased — which
never matches the stored token, returning zero or null.

## Examples

- **"...cold call source?"** — generated filter: `lead_source ILIKE '%cold
  call%'` (with a space). Stored value: `ColdCall`. Result: 0 rows.
  Expected: `3025`. Same mechanism for the `EventBooth` (expected `3010`)
  and `WebForm` (expected `2969`) variants.
- **"...percentage were accepted?"** — generated filter: `quote_status IN
  ('accepted', 'rejected', 'expired')` (lowercase). Stored value:
  `Accepted` (capitalized). Result: 0 decided lines, "percentage cannot be
  calculated." Expected: `32.99`.
- A deals-by-product-category breakdown filtered on `ColdCall` leads
  cascades the same zero-row result into every category of what should be
  a four-category breakdown.

Every answer in this category reasons coherently from a query that
returned nothing ("no leads were found... this could mean the source
isn't tracked under that label") — the response logic is sound, the query
behind it just doesn't match anything.

**Full list (17):** `SALES-T1-03-10`, `SALES-T1-03-11`, `SALES-T1-03-14`,
`SALES-T1-06-22`, `SALES-T1-06-23`, `SALES-T1-06-26`, `SALES-T2-08-35`,
`SALES-T2-10-38`, `SALES-T3-05-28`, `SALES-T3-05-29`, `SALES-T4-03-09`,
`SALES-T4-03-13`, `SALES-T4-06-23`, `SALES-T4-06-27`, `SALES-RG-02-V01`,
`SALES-RG-07-V01`, `SALES-RG-15-V01`.
