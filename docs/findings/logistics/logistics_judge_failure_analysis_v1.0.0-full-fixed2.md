# Logistics Judge Failure Analysis — `v1.0.0-full-fixed2`

**Run:** `release/v1.0.0-full-fixed2/logistics/judge/20261011T080231Z/results.json`
**Exact match:** 164 pass / 16 fail / 180 eligible — **91.11%**

This domain is already **above the ≥90% target**. The 16 remaining failures
below are the gap to close, not an urgent gate failure — framed accordingly.

Every failing `question_id` is assigned to exactly one category. Counts sum
to 16.

**Plain-language, root-cause write-ups** — one per category below:

- [Category 1 — percentage-change figure omitted](logistics_category1_missing_percentage_v1.0.0-full-fixed2.md)
- [Category 2 — a change value loses its sign](logistics_category2_unsigned_change_value_v1.0.0-full-fixed2.md)
- [Category 3 — a correct value reads as missing in a long, multi-section answer](logistics_category3_label_attachment_distance_v1.0.0-full-fixed2.md)
- [Category 4 — materially wrong computed values](logistics_category4_wrong_computed_values_v1.0.0-full-fixed2.md)
- [Category 5 — no-data result where real data exists](logistics_category5_null_result_v1.0.0-full-fixed2.md)
- [Category 6 — a correct value is reported as missing](logistics_category6_self_contradiction_v1.0.0-full-fixed2.md)
- [Category 7 — NULL rows counted together with orphan rows](logistics_category7_null_vs_orphan_v1.0.0-full-fixed2.md)

---

## Category 1 — Percentage-change figure omitted (9 rows)

For this domain's "how did X change from Q1 2026 to Q2 2026" question family
(`expected_answer` shape: `Q1 value | Q2 value | absolute change | percentage
change | direction`), the answer states the Q1/Q2 values and
the absolute change, but in 9 of 11 such questions it **never states the
percentage-change number at all** — not in a different format, not
rounded differently, genuinely absent from the text. Verified by searching
the full (untruncated) `actual_answer` for any `%`-suffixed numeral: none
exists in these 9 responses.

Rows: `LOG-T5-02-06`, `LOG-T5-02-07`, `LOG-T5-02-08`, `LOG-T5-02-09`,
`LOG-T5-05-13`, `LOG-T5-05-14`, `LOG-T5-05-15`, `LOG-T5-05-16`,
`LOG-RG-14-V01`.

Worked example (`LOG-T5-02-09`, NA region order value Q1→Q2):
expected `72296800.92 | 3492949.09 | -68803851.83 | -95.17 | down`. The
answer states "Total order value for orders assigned to NA region
warehouses fell sharply ... dropping from $72,296,800.92 ... to
$3,492,949.09 ..., a change of -$68,803,851.83" — three of five values
exact, direction correctly conveyed in prose ("fell sharply"), but `-95.17`
appears nowhere. Judge `factual_correctness: 4`, rationale: "the percentage
change (-95.17%) ... not explicitly stated ... but the core numeric facts
are all correct."

Four of these nine rows (`LOG-T5-02-07`, `LOG-T5-02-08`, `LOG-T5-05-14`,
`LOG-T5-05-16`, `LOG-RG-14-V01` — five, not four) also have the issue
described in Category 2 below; they're counted here because the missing
percentage is independently sufficient on its own, even setting the
Category 2 issue aside.

---

## Category 2 — A change value loses its sign (1 row)

In the same trend-question family as Category 1, one row's percentage
change is stated explicitly and signed; the absolute-change figure in the
same answer is instead stated as an unsigned, rounded magnitude.

Row: `LOG-T5-05-12` — expected `59449126.42 | 2345667.61 | -57103458.81 |
-96.05 | down`. The answer: "...dropping from about $59,449,126 in Q1 to
about $2,345,668 in Q2 — a drop of roughly $57,103,459, or about -96.05%."
The percentage (`-96.05%`) is stated explicitly and passes; the Q1/Q2
values round-trip correctly; only the unsigned, whole-dollar-rounded
`$57,103,459` fails against signed `-57103458.81`. This is the **only** row
in this domain where this specific gap is the sole blocker — in every
other row with the same symptom, the Category 1 issue (missing
percentage) is also present.

---

## Category 3 — A correct value reads as missing in a long, multi-section answer (1 row)

Row: `LOG-T4-02-08` ("For shipments carried by rail carriers, how does
shipment count break down by order status?"). Judge `factual_correctness: 5`
— independently confirms all five values (Delivered 15019, Shipped 6794,
Returned 3491, Pending 3084, Cancelled 2023) are stated correctly in the
response. `exact_match_detail`: `label 'Delivered' present but
2432 chars from any of its values (limit 120)`, plus three more labels
with "a different label claims its value."

The response is long (2,432+ chars) and covers three
breakdowns (order status, carrier, shipment month) in one answer, each
introducing its own set of numbers. The correct "15,019 are marked
Delivered" pairing sits early and close together, but the proximity-based
check for whether a label and its value belong together ends up matching a
*different* occurrence of one of these label words elsewhere in the longer
response (the carrier and month sections reuse enough common words/numbers
that this becomes ambiguous) — the same class of multi-occurrence,
which-occurrence-is-structural ambiguity previously identified for Project
Management's "billable"/"non-billable" case, where a general fix was
deliberately not attempted because it risked turning other currently-correct
matches into false ones.

---

## Category 4 — Materially wrong computed values (1 row)

Row: `LOG-RG-13-V01` ("For
orders assigned to an APAC warehouse, compare total order value in the
previous quarter with the quarter before it" — a rephrase variant of
`LOG-T5-02-06`). Expected `79421787.31 | 18610.50 | -79403176.81 | -99.98 |
down`. The response reports the **prior quarter as $0.00** and the quarter
before that as $18,610.50 — i.e. it has the two periods' values backwards
and zeroed, rather than $79.4M → $18.6K. Judge `factual_correctness: 1`,
confirming this is a genuine computation/period-alignment inconsistency,
not a presentation issue. This is also one of three rephrase-group
findings `results.json`'s own `rephrase_groups.platform_findings` flags for
this domain (`LOG-RG-13`): the base question and its two variants resolve
to different ground truth.

---

## Category 5 — Null/no-data result where real data exists (1 row)

Row: `LOG-T4-06-21`
("For finished goods inventory, how does total quantity on hand break down
by warehouse region?"). Expected `LATAM | 15982400; EU | 11560637; APAC |
11380724; NA | 8952671`. The response states "No data was found... a
single result with a null value... no breakdown by warehouse region was
available." Judge `factual_correctness: 1`. This question is
also the base of rephrase group `LOG-RG-11`, whose variant (`LOG-RG-11-
V01`) resolved a full four-region breakdown — the failure here is tied to
this exact phrasing, not to data availability.

---

## Category 6 — A correct value is reported as missing (2 rows)

Rows: `LOG-T4-06-22`
("hardware inventory... by warehouse region"), `LOG-T4-06-24` ("supplies
inventory... by warehouse region"). In both, three of four region values
match the expected answer exactly (LATAM, EU, APAC), but the response
explicitly states the fourth (NA) "was not present in the provided
breakdown" / "cannot confirm" — even though the correct NA figure is
present elsewhere in the same response's broader all-category breakdown.
Judge `factual_correctness: 3` on both: "three of four values are correct,
but [the response] states the required value is missing/unknown rather
than providing it." The response contradicts its own data — the number
genuinely isn't attached to the NA/hardware (or NA/supplies) label
anywhere in the text.

---

## Category 7 — Wrong aggregation, NULL rows counted as orphan rows (1 row)

Row: `LOG-T3-09-32` ("How
many shipped orders reference a `warehouse_id` that does not match any real
warehouse record?"). Expected `160`. The answer gives `678`, explicitly
breaking it down as "518 orders have no warehouse ID at all (a
missing/blank reference)" plus the remainder genuinely orphaned. Judge
`factual_correctness: 1`, rationale: the query behind this answer uses a
"not found" style check, which (per standard SQL `NULL` comparison
semantics) also matches rows with a `NULL` `warehouse_id` — folding the
dataset's **2.5% NULL-`warehouse_id`** imperfection rate into the
**1% orphan-`warehouse_id`** imperfection rate the question is actually
asking about (see `schemas/logistics/logistics_semantics.md`). The
expected answer isolates true orphans only; the response's query conflates
the two deliberately distinct imperfection categories.

---

## Summary

| Category | Count |
|---|---|
| 1 — Percentage-change omitted | 9 |
| 2 — Change value loses its sign | 1 |
| 3 — Correct value reads as missing in a long response | 1 |
| 4 — Materially wrong computed values | 1 |
| 5 — Null/no-data where real data exists | 1 |
| 6 — Correct value reported as missing | 2 |
| 7 — NULL rows counted as orphan rows | 1 |
| **Total** | **16** |

No category here is a dataset/Q&A-pair defect: every
`expected_answer` checked against `reference_sql`/`logistics_semantics.md`
is internally consistent. Categories 1, 4, 5, 6, and 7 (14 rows) describe
the response computing, omitting, or contradicting a value. Categories 2
and 3 (2 rows) describe a mismatch between how a value is presented and
how this evaluation checks for it — pointed out here without assuming
which side should change.
