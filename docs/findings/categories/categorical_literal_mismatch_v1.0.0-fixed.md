# Categorical Literal Mismatch (55 questions — CRM 11, Sales 20, Finance 2, Project Management 22)

The single largest fixable-shaped pattern across the whole evaluation.
Categorical columns (status, type, location, industry, source) are
stored as compact single-word or CamelCase tokens — `GeneralInquiry`,
`ColdCall`, `InProgress`, `FinancialServices`, `AccountsPayable`. The
platform's generated SQL filter guesses a human-readable rendering
instead — a space, a hyphen, or lowercase — which never matches the
real stored value. The query runs cleanly, returns zero rows, and the
answer reports "no data," even though the data exists in large
quantities. Confirmed in every domain below by comparing the platform's
own generated SQL against the stored distinct values directly.

## CRM (11)

`GeneralInquiry`, `ServiceRequest`, `EmailOpen`, `EventAttendance`,
`Reengagement`, `FinancialServices` are each stored with no space; the
platform's filters use `'general inquiry'`, `'financial services'`,
`'re-engagement'`, etc.

Example: "How many support cases fall under general inquiry?" — filter
used `category = 'general inquiry'`; stored value `GeneralInquiry`.
Result: 0 rows reported as "no data." Real answer: 23,938.

Affected: `CRM-T1-04-16`, `CRM-T1-04-18`, `CRM-T1-05-19`, `CRM-T1-05-20`,
`CRM-T2-03-12`, `CRM-T2-04-20`, `CRM-T2-07-30`, `CRM-T2-08-37`,
`CRM-T3-02-08`, `CRM-T3-05-28`, `CRM-RG-15-V01`.

(CRM also has 11 further breakdown/ranking questions that return a flat
zero instead of a per-category split, which plausibly share this same
root cause on a bigger query — but that specific mechanism wasn't
independently re-verified with the same rigor as the 11 above, so
they're kept under "Breakdown/Total Computed via Different Logic"
instead rather than asserted here without proof.)

## Sales (20)

`lead_source` stored as `ColdCall`/`EventBooth`/`WebForm`; `quote_status`
stored as `Accepted`/`Rejected`/`Expired` (fixed capitalization). The
platform's filters use spaced (`'%web form%'`) or lowercase
(`'accepted'`) renderings.

Example: "Deals from web form leads" — filter `lead_source ILIKE '%web form%'`
vs. stored `WebForm`. Expected 968; answered 0. Same mechanism also
produces a mirror-image failure: "leads that never became a deal"
answers 0 (claims every lead converted) because the same broken filter
finds nothing to exclude.

Affected: `SALES-T1-03-10`, `SALES-T1-03-11`, `SALES-T1-03-14`,
`SALES-T1-06-22`, `SALES-T1-06-23`, `SALES-T1-06-26`, `SALES-T2-01-05`,
`SALES-T2-08-33`, `SALES-T2-10-38`, `SALES-T2-10-40`, `SALES-T3-05-28`,
`SALES-T3-05-32`, `SALES-T4-03-09`, `SALES-T4-03-10`, `SALES-T4-03-13`,
`SALES-T4-06-23`, `SALES-T4-06-27`, `SALES-RG-02-V01`, `SALES-RG-07-V01`,
`SALES-RG-15-V01`.

## Finance (2)

`accounts.account_subtype` stored as `AccountsPayable` (no space); the
generated filter uses a spaced rendering.

- "How many accounts are classified as accounts payable accounts?" —
  expected 20; answered "no accounts... the count comes back as 0."
- "Combined budget for accounts payable accounts" — expected
  2,987,315.56; answered "No budget data was found... null."

Affected: `FIN-T1-08-25`, `FIN-RG-16-V01`.

## Project Management (22)

`tasks.status` stored as `NotStarted`/`InProgress`/`Blocked`/`Completed`;
`projects.status` as `OnHold`/`Active`/`Planning`/`Cancelled`;
`resources.location` as `AsiaPacific`/`NorthAmerica`/`LatinAmerica`/
`Europe`. Only the multi-word, CamelCase-stored values are affected —
single-word values aren't.

Example: "How many tasks are currently in in progress status?" — filter
`status ILIKE 'in progress'` vs. stored `InProgress`. Expected 3,408;
answered 0.

Status-filter affected: `PM-T1-03-12`, `PM-T1-03-13`, `PM-T2-01-04`,
`PM-T2-03-12`, `PM-T2-03-13`, `PM-T2-05-22`, `PM-T2-06-26`, `PM-T2-06-27`,
`PM-T3-03-14`, `PM-T3-04-19`, `PM-T4-01-03`, `PM-T4-01-04`, `PM-T4-05-22`,
`PM-T4-06-26`, `PM-T4-06-27`, `PM-T5-01-04`, `PM-T5-06-20`, `PM-T5-07-23`,
`PM-T5-07-24`. Location-filter affected: `PM-T2-09-35`, `PM-T2-09-37`,
`PM-T3-09-31`.

## Where to look these up

Search by `question_id` in these two files per domain — the first has
the full question text and expected answer, the second has the
platform's actual answer, `exact_match_detail`, and judge scores:

| Domain | Question + expected answer | Platform's answer + judge scores |
|---|---|---|
| CRM | `release/v1.0.0-fixed/crm/qa_pairs/crm_qa_pairs.csv` | `release/v1.0.0-fixed/crm/judge/20261014T033522Z/results.json` |
| Sales | `release/v1.0.0-fixed/sales/qa_pairs/sales_qa_pairs.csv` | `release/v1.0.0-fixed/sales/judge/20261013T102604Z/results.json` |
| Finance | `release/v1.0.0-fixed/finance/qa_pairs/finance_qa_pairs.csv` | `release/v1.0.0-fixed/finance/judge/20261013T112105Z/results.json` |
| Project Management | `release/v1.0.0-fixed/project_management/qa_pairs/project_management_qa_pairs.csv` | `release/v1.0.0-fixed/project_management/judge/20261013T174157Z/results.json` |

## Status

Not fixable in this evaluation's scorer or Q&A pairs — the text is read
correctly in every case; the SQL built to answer the question is what's
wrong. Two routes exist, on different sides:

- **Dataset side**: re-encode the affected categorical columns with
  human-readable values (e.g. `"Cold Call"` with a space) across all
  four domains above. A real fix, but a full dataset + Q&A regeneration,
  not a quick patch — already flagged as a deferred decision pending
  explicit go-ahead.
- **Platform side**: normalize a category/status/location filter value
  against the real distinct values a column contains before running the
  query (or add a case/space-insensitive fallback when an exact match
  returns zero rows), rather than guessing a human-rendered literal.

This is the single highest-leverage item in the whole analysis — one
fix, on either side, closes up to 55 questions across four domains.
