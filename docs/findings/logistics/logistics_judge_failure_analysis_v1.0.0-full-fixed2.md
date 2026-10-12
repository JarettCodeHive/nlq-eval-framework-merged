# Logistics Judge Failure Analysis — `v1.0.0-full-fixed2`

**Run:** `release/v1.0.0-full-fixed2/logistics/judge/20261011T080231Z/results.json`
**Exact match:** 164 pass / 16 fail / 180 eligible — **91.11%**

This domain is already **above the ≥90% target**. The 16 remaining failures
below are the gap to close, not an urgent gate failure — framed accordingly.

Every failing `question_id` is assigned to exactly one category. Counts sum
to 16.

---

## Category 1 — Platform-side: percentage-change figure omitted (9 rows)

**Fixability: not fixable in this codebase.** The deterministic scorer
requires every field in `expected_answer` to appear in the platform's prose.
For this domain's "how did X change from Q1 2026 to Q2 2026" question family
(`expected_answer` shape: `Q1 value | Q2 value | absolute change | percentage
change | direction`), the platform's narrative states the Q1/Q2 values and
the absolute change, but in 9 of 11 such questions it **never prints the
percentage-change number at all** — not in a different format, not
rounded differently, genuinely absent from the text. Verified by searching
the full (untruncated) `actual_answer` for any `%`-suffixed numeral: none
exists in these 9 responses. No scorer change can pass a value that isn't in
the text.

Rows: `LOG-T5-02-06`, `LOG-T5-02-07`, `LOG-T5-02-08`, `LOG-T5-02-09`,
`LOG-T5-05-13`, `LOG-T5-05-14`, `LOG-T5-05-15`, `LOG-T5-05-16`,
`LOG-RG-14-V01`.

Worked example (`LOG-T5-02-09`, NA region order value Q1→Q2):
expected `72296800.92 | 3492949.09 | -68803851.83 | -95.17 | down`. The
platform states "Total order value for orders assigned to NA region
warehouses fell sharply ... dropping from $72,296,800.92 ... to
$3,492,949.09 ..., a change of -$68,803,851.83" — three of five values
exact, direction correctly conveyed in prose ("fell sharply"), but `-95.17`
appears nowhere. Judge `factual_correctness: 4`, rationale: "the percentage
change (-95.17%) ... not explicitly stated ... but the core numeric facts
are all correct."

Four of these nine rows (`LOG-T5-02-07`, `LOG-T5-02-08`, `LOG-T5-05-14`,
`LOG-T5-05-16`, `LOG-RG-14-V01` — five, not four) also fail on the
absolute-change value for the reason in Category 2 below; they're counted
here because the missing percentage is independently sufficient to fail
them even if the change-value gap were fixed, so fixing Category 2 alone
would not flip these specific rows to pass.

**What a resolution would look like, if ever pursued:** this isn't a
scorer fix — it would mean asking the platform (prompt/product side) to
always state a computed percentage change when a question asks for one, or
relaxing the Q&A pair's `expected_answer` contract for this question family
to not require the percentage as a literal token. Neither is a change to
`judge/exact_match.py`.

---

## Category 2 — Scorer-side: unsigned/rounded "change" value not covered by the existing negative-numeric fallback (1 row)

**Fixability: scorer-fixable, bounded, low risk.** `judge/exact_match.py`
already has a sign-flexible fallback for one field in a trend record (the
percentage-change field, paired with the trailing direction label) added
earlier in this project's history. It is not applied to the *absolute
change* field in the same record. When the platform phrases that figure as
an unsigned, rounded magnitude ("a drop of roughly $X") instead of a
literal negative number, the scorer fails it even though the percentage
and direction both check out.

Row: `LOG-T5-05-12` — expected `59449126.42 | 2345667.61 | -57103458.81 |
-96.05 | down`. Platform: "...dropping from about $59,449,126 in Q1 to
about $2,345,668 in Q2 — a drop of roughly $57,103,459, or about -96.05%."
The percentage (`-96.05%`) is stated explicitly and passes; the Q1/Q2
values round-trip correctly; only the unsigned, whole-dollar-rounded
`$57,103,459` fails against signed `-57103458.81`. This is the **only** row
in this domain where extending the existing fallback to the change field
would flip the row to pass outright — in every other row with the same
symptom, a second, independent blocker (Category 1's missing percentage)
also fails the row, so the fix would be correct to make but wouldn't by
itself move this domain's pass count beyond +1.

---

## Category 3 — Scorer-side: label-value attachment distance exceeded in a long multi-section response (1 row)

**Fixability: scorer-fixable in principle, but the same risk already
documented for Project Management applies — not a safe quick fix.** Row:
`LOG-T4-02-08` ("For shipments carried by rail carriers, how does shipment
count break down by order status?"). Judge `factual_correctness: 5` —
independently confirms all five values (Delivered 15019, Shipped 6794,
Returned 3491, Pending 3084, Cancelled 2023) are stated correctly in the
platform's answer. `exact_match_detail`: `label 'Delivered' present but
2432 chars from any of its values (limit 120)`, plus three more labels
with "a different label claims its value."

The platform's response is long (2,432+ chars) and covers three
breakdowns (order status, carrier, shipment month) in one answer, each
introducing its own set of numbers. The correct `"15,019 are marked
Delivered"` pairing sits early and close together, but the scorer's
`_find_label()`/`_label_owns_a_value()` appears to be matching a *different*
occurrence of one of these label words elsewhere in the longer response
(the carrier and month sections reuse enough common words/numbers that
ownership becomes ambiguous), the same class of multi-occurrence,
which-occurrence-is-structural ambiguity previously investigated and
**deliberately left unfixed** for Project Management's "billable"/"non-
billable" case, because a general fix risks turning other currently-correct
passes into false passes. The honest resolution recorded here is the same
one recorded there: a real scorer gap, not a one-line fix, and not
attempted blind.

---

## Category 4 — Platform-side: materially wrong computed values (1 row)

**Fixability: not fixable in this codebase.** Row: `LOG-RG-13-V01` ("For
orders assigned to an APAC warehouse, compare total order value in the
previous quarter with the quarter before it" — a rephrase variant of
`LOG-T5-02-06`). Expected `79421787.31 | 18610.50 | -79403176.81 | -99.98 |
down`. The platform reports the **prior quarter as $0.00** and the quarter
before that as $18,610.50 — i.e. it has the two periods' values backwards
and zeroed, rather than $79.4M → $18.6K. Judge `factual_correctness: 1`,
confirming this is a genuine platform computation/period-alignment error,
not a presentation issue. This is also one of three rephrase-group
findings `results.json`'s own `rephrase_groups.platform_findings` flags for
this domain (`LOG-RG-13`): the base question and its two variants resolve
to different ground truth, which independently corroborates that the
platform — not the scorer — is inconsistent here.

---

## Category 5 — Platform-side: null/no-data result where real data exists (1 row)

**Fixability: not fixable in this codebase.** Row: `LOG-T4-06-21`
("For finished goods inventory, how does total quantity on hand break down
by warehouse region?"). Expected `LATAM | 15982400; EU | 11560637; APAC |
11380724; NA | 8952671`. The platform returned "No data was found... a
single result with a null value... no breakdown by warehouse region was
available." Judge `factual_correctness: 1` — a genuine platform
query/join failure (likely a filter or join condition that excluded all
finished-goods rows), not a scoring artifact. Side note: this question is
also the base of rephrase group `LOG-RG-11`, whose variant (`LOG-RG-11-
V01`) resolved a full four-region breakdown — further evidence the
platform's failure here is query-construction-specific to this exact
phrasing, not a data-availability problem.

---

## Category 6 — Platform-side: platform self-reports a value as missing that it already computed correctly elsewhere (2 rows)

**Fixability: not fixable in this codebase.** Rows: `LOG-T4-06-22`
("hardware inventory... by warehouse region"), `LOG-T4-06-24` ("supplies
inventory... by warehouse region"). In both, three of four region values
match the expected answer exactly (LATAM, EU, APAC), but the platform's own
prose explicitly states the fourth (NA) "was not present in the provided
breakdown" / "cannot confirm" — even though the correct NA figure is
present elsewhere in the same response's broader all-category breakdown.
Judge `factual_correctness: 3` on both: "three of four values are correct,
but [the platform] states the required value is missing/unknown rather
than providing it." This is the platform contradicting its own data, not a
scorer-side false negative — the number genuinely isn't attached to the
NA/hardware (or NA/supplies) label anywhere the scorer could credit it.

---

## Category 7 — Platform-side: wrong aggregation, NULL rows counted as orphan rows (1 row)

**Fixability: not fixable in this codebase.** Row: `LOG-T3-09-32` ("How
many shipped orders reference a `warehouse_id` that does not match any real
warehouse record?"). Expected `160`. Platform answered `678`, explicitly
breaking it down as "518 orders have no warehouse ID at all (a
missing/blank reference)" plus the remainder genuinely orphaned. Judge
`factual_correctness: 1`, rationale: the platform's SQL uses `NOT EXISTS`,
which (per standard SQL `NULL` semantics) also matches rows with a `NULL`
`warehouse_id` — folding the dataset's **2.5% NULL-`warehouse_id`**
imperfection rate into the **1% orphan-`warehouse_id`** imperfection rate
the question is actually asking about (see
`schemas/logistics/logistics_semantics.md`). The expected answer isolates
true orphans only; the platform's query conflates the two deliberately
distinct imperfection categories. A platform prompt/schema-retrieval issue,
not something `judge/exact_match.py` can correct.

---

## Summary

| Category | Count | Fixability |
|---|---|---|
| 1 — Percentage-change omitted from platform prose | 9 | Platform-only |
| 2 — Unsigned/rounded "change" value, fallback gap | 1 | **Scorer-fixable** |
| 3 — Label-value attachment distance in long response | 1 | Scorer-side, high-risk to fix (declined, same reasoning as PM) |
| 4 — Materially wrong computed values | 1 | Platform-only |
| 5 — Null/no-data where real data exists | 1 | Platform-only |
| 6 — Platform self-reports correct value as missing | 2 | Platform-only |
| 7 — NULL rows counted as orphan rows | 1 | Platform-only |
| **Total** | **16** | |

Of 16 failures: **1 is cleanly scorer-fixable** (Category 2, would move this
domain from 164/180 to 165/180 pass), **1 more is a scorer defect but not
safely fixable without new false-pass risk** (Category 3), and the
remaining **14 are platform-side** — the platform either omitted a
required computed figure, computed the wrong number, or contradicted data
it already had. No category here is a dataset/Q&A-pair defect: every
`expected_answer` checked against `reference_sql`/`logistics_semantics.md`
is internally consistent.
