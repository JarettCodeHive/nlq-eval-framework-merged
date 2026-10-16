# Scoped Filter Silently Dropped (7 questions — Finance 4, Logistics 2, Project Management 1)

Each question includes a scoping condition — a status, a source system,
an FX-rate source, a product category, a project status — that the
answer either doesn't apply, or applies to the wrong half of a join (an
outer join where unmatched rows should count as "no match," but get
treated as if the join condition didn't exist). The result is a bigger,
unscoped answer where a narrower one was asked for.

## Finance (4)

- "How many reversed transactions have never been posted to the
  ledger?" (`FIN-T3-06-19`) Expected 132. Answer: "0 — every transaction
  has gone through the posting process."
- "...from Billing, unposted?" (`FIN-RG-07-V01`) Expected 1,264. Answer:
  "0 — all Billing system transactions have been posted."
- A question scoped to FX rates sourced "via synthetic daily" data
  (`FIN-T3-11-26`) expects 1,102 unmatched records; the answer claims 0,
  the source-filter scope dropped somewhere in the query — the same
  mechanism as its `SyntheticEstimated` sibling, which happened to
  answer correctly this run instead.

## Logistics (2)

`LOG-T4-06-22` ("hardware inventory by warehouse region") and
`LOG-T4-06-24` ("supplies inventory by warehouse region") both ask for a
quantity-on-hand breakdown restricted to one product category. Both
responses explicitly say they couldn't isolate that category and
instead report the combined total across every product category —
249,651,209 units in both responses, word for word the same grand
total. (`LOG-T4-06-24`'s all-category regional totals happen to
coincide with 3 of its 4 category-specific expected values by chance,
so its `exact_match_detail` only flags one mismatch; the underlying
defect is identical in both rows.)

## Project Management (1)

`PM-T4-05-23` — a breakdown scoped to "planning projects" expects a
~16,000 total; the answer gives a 151,500-entry grand total, an order
of magnitude too large to be one status's slice. (A second question
with the same shape, scoped to "analyst resources," failed this way in
the previous release but passed this run — the platform correctly
applied that scope this time.)

## Where to look these up

Search by `question_id` in these two files per domain — the first has
the full question text and expected answer, the second has the
platform's actual answer, `exact_match_detail`, and judge scores:

| Domain | Question + expected answer | Platform's answer + judge scores |
|---|---|---|
| Finance | `release/v1.0.0-fixed/finance/qa_pairs/finance_qa_pairs.csv` | `release/v1.0.0-fixed/finance/judge/20261013T112105Z/results.json` |
| Logistics | `release/v1.0.0-fixed/logistics/qa_pairs/logistics_qa_pairs.csv` | `release/v1.0.0-fixed/logistics/judge/20261013T160913Z/results.json` |
| Project Management | `release/v1.0.0-fixed/project_management/qa_pairs/project_management_qa_pairs.csv` | `release/v1.0.0-fixed/project_management/judge/20261013T174157Z/results.json` |

## Status

Not fixable in this evaluation — the scoping condition named in the
question doesn't make it into the query the platform actually runs.
When a question says "have never been X," "sourced via Y," or "for
[status] projects," that's asking for the complement of a join or a
label-restricted subset; the fix is on the platform's query-generation
side, to make sure that restriction survives into the executed query
rather than silently falling away.
