# CRM — Category 1: Zero Results Returned for Real Data (12 questions)

This explains, in plain terms, why 12 of
the failing CRM questions got a "0" or "no data" answer even though the
data clearly exists.

## What's happening

The data stores category-type values as single compact words, e.g.
`GeneralInquiry`, `ServiceRequest`, `EmailOpen`, `Reengagement`,
`FinancialServices`. When your model builds its SQL filter, it writes the
*human-readable* version of that value instead — adding a space or a
hyphen, e.g. `'general inquiry'`, `'re-engagement'`, `'financial
services'`. A filter like `category = 'general inquiry'` or `category
ILIKE 'general inquiry'` will **never match** a stored value of
`GeneralInquiry`, because the space is a real character in the comparison,
not something SQL ignores. The query runs fine, returns zero rows, and the
app correctly reports "no data" — the real problem is a step earlier, when
the filter value was constructed.

## Example

**Question:** "How many support cases fall under general inquiry?"
**Your SQL:** `WHERE category ILIKE 'general inquiry'`
**Stored value:** `GeneralInquiry`
**Result:** 0 rows → reported as "no data found"
**Actual answer:** 23,938 cases

A breakdown-by-category query on the same table (no literal filter, just
`GROUP BY category`) correctly surfaces `GeneralInquiry → 23,938` — proving
the data was always there; only the direct filter failed.

## Where this showed up

| Question | Filter your SQL used | Stored value |
|---|---|---|
| Support cases — general inquiry | `'general inquiry'` | `GeneralInquiry` |
| Support cases — service request | `'Service Request'` | `ServiceRequest` |
| Interactions — email open events | `'%email open%'` | `EmailOpen` |
| Interactions — event attendance | `'event attendance'` | `EventAttendance` |
| Interactions — form submission | `'%form submission%'` | `FormSubmission` |
| Interactions — email opens (rephrase) | `'email open%'` | `EmailOpen` |
| Campaign budget — re-engagement | `'%re-engagement%'` | `Reengagement` |
| Interactions — re-engagement campaigns | `'%re-engagement%'` | `Reengagement` |
| Re-engagement campaigns w/ no interactions | `'%re-engagement%'` | `Reengagement` |
| Contacts — financial services | `'%financial services%'` | `FinancialServices` |
| SLA rate — financial services | `'%financial services%'` | `FinancialServices` |

One question in this set (`CRM-T2-07-30`, "event interactions from Business
Analyst contacts") has a different flavor of the same root problem: the
contact title filter (`'Business Analyst'`) matched fine, but the model
guessed a category value of `'Event'` for the interaction type — that
value doesn't exist at all; the real one is `EventAttendance`. Same lesson
— don't guess the category spelling, look it up.

## Suggested fix

Before filtering on a category/status/industry/type column, look up (or
cache) the **exact distinct values** that column actually contains, and
match the user's wording to the closest real value — rather than
reformatting the user's own phrasing into a guessed literal. A simple,
cheap version of this: use a case-insensitive, **space/hyphen-stripped**
comparison (`REPLACE(LOWER(category), ' ', '') = REPLACE(LOWER('general
inquiry'), ' ', '')`) as a fallback when an exact match returns nothing,
before concluding "no data."
