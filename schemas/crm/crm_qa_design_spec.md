# CRM Q&A Design Spec

Status: Draft v0.1 - pending sign-off

Source of truth:

- ERD: `schemas/crm/crm_er.dbml`
- DDL: `schemas/crm/crm_ddl.sql`
- CSV header spec: `schemas/crm/crm_csv_header_spec.md`
- Generation config entry point: `config/generation/crm.json`
- Schema and relationships: `config/generation/crm/schema.json`
- Join paths and consistency rules: `config/generation/crm/validation.json`

Purpose:

This document defines the CRM natural-language question design contract before
final Q&A authoring. It identifies the supported table paths, question patterns,
expected SQL shapes, ambiguity rules, and data imperfections that benchmark
questions may intentionally exercise.

## Dataset Scope

The CRM dataset contains these release CSVs:

1. `accounts.csv`
2. `contacts.csv`
3. `opportunities.csv`
4. `contact_opportunities.csv`
5. `activities.csv`
6. `interactions.csv`

Primary business concepts:

- Account ownership, account size, industry, region, and active status.
- Contact assignment to accounts, including contacts with no account.
- Opportunity pipeline, stage, deal amount, close dates, and loss reason.
- Explicit contact-to-opportunity many-to-many associations.
- Activities by account, opportunity, contact, type, status, and date.
- Contact interactions by channel, type, timestamp, and account.

## Fixed Semantic Rules

- Revenue and pipeline value must be derived from `opportunities.deal_amount`.
- Do not use a denormalized account revenue field; none exists by design.
- `currency_code` is currently fixed to `USD` for generated CRM data.
- Closed-won questions use `opportunities.stage = 'Closed Won'`.
- Closed-lost questions use `opportunities.stage = 'Closed Lost'`.
- `opportunities.loss_reason` should be interpreted only for closed-lost
  opportunities.
- Open opportunity questions should exclude `Closed Won` and `Closed Lost`
  unless the question explicitly says otherwise.
- Account owner questions use `accounts.owner_name`.
- Date filters on opportunity closure use `opportunities.close_date`.
- Expected-close questions use `opportunities.expected_close_date`.
- Activity date filters use `activities.activity_date`.
- Interaction date filters use `interactions.interaction_at`.
- NULL values must be handled intentionally; empty CSV fields represent SQL NULL.

## Supported Join Paths

| ID | Name | Join Type | Tables | Required Behavior |
|---|---|---|---|---|
| `crm_jp_001` | Accounts to opportunities | INNER | `accounts`, `opportunities` | Produces non-empty results. |
| `crm_jp_002` | Accounts to contacts | LEFT | `accounts`, `contacts` | Includes at least one account without contacts. |
| `crm_jp_003` | Accounts to activities | INNER | `accounts`, `activities` | Produces non-empty results. |
| `crm_jp_004` | Contacts to interactions | INNER | `contacts`, `interactions` | Produces non-empty results. |
| `crm_jp_005` | Accounts to interactions | LEFT | `accounts`, `interactions` | Includes at least one account without direct interactions. |
| `crm_jp_006` | Contacts to activities | LEFT | `contacts`, `activities` | Includes at least one contact without linked activities. |
| `crm_jp_007` | Opportunities to activities | LEFT | `opportunities`, `activities` | Includes at least one opportunity without linked activities. |
| `crm_jp_008` | Accounts to opportunities to activities | INNER | `accounts`, `opportunities`, `activities` | Supports three-table account, opportunity, and activity questions. |
| `crm_jp_009` | Contacts to contact opportunities to opportunities | INNER | `contacts`, `contact_opportunities`, `opportunities` | Supports explicit many-to-many contact/opportunity questions. |

## Question Coverage Matrix

| Category | Target Tier | Required? | Main Tables | Notes |
|---|---|---:|---|---|
| Single-table filter | T1 | Yes | Any table | Tests simple predicates, NULL checks, and enum filters. |
| Aggregation | T2 | Yes | `opportunities`, `activities`, `interactions` | Tests `COUNT`, `SUM`, `AVG`, `MIN`, `MAX`. |
| Group by | T2 | Yes | `accounts`, `opportunities`, `activities` | Common dimensions: owner, region, industry, stage, activity type. |
| INNER JOIN | T2/T4 | Yes | `accounts`, `opportunities`, `activities`, `contacts`, `interactions` | Uses required non-null FK paths. |
| LEFT JOIN unmatched rows | T3/T4 | Yes | `accounts`, `contacts`, `interactions`, `activities`, `opportunities` | Must use `LEFT JOIN` and `IS NULL` predicates correctly. |
| Three-table join | T4/T5 | Yes | `accounts`, `opportunities`, `activities` | Tests multi-hop SQL generation. |
| Many-to-many join | T4/T5 | Yes | `contacts`, `contact_opportunities`, `opportunities` | Must use the explicit junction table. |
| Date filtering | T2/T4 | Yes | `opportunities`, `activities`, `interactions` | Uses generated date fields and fixed reference-date logic where needed. |
| Imperfection-aware | T3/T5 | Yes | `contacts`, `opportunities`, `activities`, `interactions` | Tests duplicates, NULL close dates, and nullable FK behavior. |
| Ranking / top-N | T2/T4 | Yes | `accounts`, `opportunities`, `activities` | Must specify deterministic ordering for ties when exact outputs are required. |

## Representative Question Templates

These templates define allowed Q&A patterns. Final benchmark questions may vary
wording but should keep the same SQL intent.

### T1 Single-Table Filters

Question:

```text
Which active accounts are in the {region} region?
```

Expected SQL pattern:

```sql
SELECT *
FROM accounts
WHERE is_active = TRUE
  AND region = {region};
```

Purpose: validates direct filters on dimension attributes.

### T2 Aggregation by Owner

Question:

```text
What is the total closed-won deal amount by account owner?
```

Expected SQL pattern:

```sql
SELECT
    a.owner_name,
    SUM(o.deal_amount) AS total_closed_won_amount
FROM accounts AS a
JOIN opportunities AS o
    ON o.account_id = a.account_id
WHERE o.stage = 'Closed Won'
GROUP BY a.owner_name
ORDER BY total_closed_won_amount DESC, a.owner_name ASC;
```

Purpose: validates `crm_jp_001`, aggregation, grouping, and deterministic order.

### T2 Activity Counts

Question:

```text
How many completed meetings does each account owner have?
```

Expected SQL pattern:

```sql
SELECT
    a.owner_name,
    COUNT(*) AS completed_meeting_count
FROM accounts AS a
JOIN activities AS act
    ON act.account_id = a.account_id
WHERE act.activity_type = 'Meeting'
  AND act.status = 'Completed'
GROUP BY a.owner_name
ORDER BY completed_meeting_count DESC, a.owner_name ASC;
```

Purpose: validates `crm_jp_003` and fact aggregation by account owner.

### T3 Accounts Without Contacts

Question:

```text
Which accounts do not have any contacts?
```

Expected SQL pattern:

```sql
SELECT a.account_id, a.account_name
FROM accounts AS a
LEFT JOIN contacts AS c
    ON c.account_id = a.account_id
WHERE c.contact_id IS NULL
ORDER BY a.account_id ASC;
```

Purpose: validates `crm_jp_002` and unmatched parent rows.

### T3 Opportunities Without Activities

Question:

```text
Which opportunities have no linked activities?
```

Expected SQL pattern:

```sql
SELECT o.opportunity_id, o.opportunity_name
FROM opportunities AS o
LEFT JOIN activities AS act
    ON act.opportunity_id = o.opportunity_id
WHERE act.activity_id IS NULL
ORDER BY o.opportunity_id ASC;
```

Purpose: validates `crm_jp_007` and nullable activity opportunity links.

### T4 Three-Table Pipeline Activity

Question:

```text
For each account owner, how many activities are linked to closed-won opportunities?
```

Expected SQL pattern:

```sql
SELECT
    a.owner_name,
    COUNT(act.activity_id) AS activity_count
FROM accounts AS a
JOIN opportunities AS o
    ON o.account_id = a.account_id
JOIN activities AS act
    ON act.opportunity_id = o.opportunity_id
WHERE o.stage = 'Closed Won'
GROUP BY a.owner_name
ORDER BY activity_count DESC, a.owner_name ASC;
```

Purpose: validates `crm_jp_008` and multi-hop join behavior.

### T4 Explicit Many-to-Many

Question:

```text
Which contacts are associated with more than one opportunity?
```

Expected SQL pattern:

```sql
SELECT
    c.contact_id,
    c.first_name,
    c.last_name,
    COUNT(co.opportunity_id) AS opportunity_count
FROM contacts AS c
JOIN contact_opportunities AS co
    ON co.contact_id = c.contact_id
GROUP BY c.contact_id, c.first_name, c.last_name
HAVING COUNT(co.opportunity_id) > 1
ORDER BY opportunity_count DESC, c.contact_id ASC;
```

Purpose: validates the explicit junction table and contact-to-opportunity
many-to-many cardinality.

### T4 Primary Contact for Opportunities

Question:

```text
Who is the primary contact for each closed-won opportunity?
```

Expected SQL pattern:

```sql
SELECT
    o.opportunity_id,
    o.opportunity_name,
    c.contact_id,
    c.first_name,
    c.last_name
FROM opportunities AS o
JOIN contact_opportunities AS co
    ON co.opportunity_id = o.opportunity_id
JOIN contacts AS c
    ON c.contact_id = co.contact_id
WHERE o.stage = 'Closed Won'
  AND co.is_primary_contact = TRUE
ORDER BY o.opportunity_id ASC;
```

Purpose: validates relationship-level attributes on the junction table.

### T5 Imperfection-Aware Duplicate Contacts

Question:

```text
Which contact names appear more than once with the same email?
```

Expected SQL pattern:

```sql
SELECT
    first_name,
    last_name,
    email,
    COUNT(*) AS duplicate_count
FROM contacts
GROUP BY first_name, last_name, email
HAVING COUNT(*) > 1
ORDER BY duplicate_count DESC, email ASC;
```

Purpose: validates near-duplicate contact imperfection handling.

### T5 NULL Close Dates

Question:

```text
How many opportunities have no close date by stage?
```

Expected SQL pattern:

```sql
SELECT
    stage,
    COUNT(*) AS null_close_date_count
FROM opportunities
WHERE close_date IS NULL
GROUP BY stage
ORDER BY null_close_date_count DESC, stage ASC;
```

Purpose: validates intentional `opportunities.close_date` NULL injection.

## Disallowed or Ambiguous Question Patterns

- Do not ask for account revenue from an `annual_revenue` column; it does not
  exist.
- Do not treat `contacts.account_id` as mandatory.
- Do not infer contact/opportunity relationships from `activities` when the
  question asks for formal opportunity contacts; use `contact_opportunities`.
- Do not ask cross-currency CRM calculations unless multi-currency generation is
  explicitly introduced in a future version.
- Do not ask time-zone-sensitive questions; CRM timestamps are generated without
  time-zone semantics.
- Do not use vague date phrases such as "recent", "old", or "stale" unless the
  exact cutoff is included in the question.
- Do not use `loss_reason` for non-closed-lost opportunity interpretation.

## Expected Answer Rules

- Count answers must be exact integers.
- Monetary answers must preserve two-decimal precision.
- Percentage answers, if introduced, must state the rounding rule in the
  individual Q&A item.
- Ranking questions must include deterministic tie-breaking in expected SQL.
- NULL-sensitive questions must use `IS NULL` or `IS NOT NULL`; equality checks
  against empty strings are not valid.
- Duplicate-sensitive questions must specify whether they mean physical rows or
  distinct business entities.

## Minimum Release Coverage

Before CRM Q&A sign-off, the final question set should include at least:

- 5 single-table questions.
- 5 aggregation or group-by questions.
- 5 required INNER JOIN questions.
- 4 LEFT JOIN unmatched-row questions.
- 3 three-table join questions.
- 3 explicit many-to-many questions using `contact_opportunities`.
- 3 date-filter questions.
- 3 imperfection-aware questions.

The same final question may satisfy more than one category if its SQL pattern
clearly exercises each required behavior.

## Sign-Off

| Role | Name | Status | Date | Notes |
|---|---|---|---|---|
| Data Engineering Lead | TBD | Pending | TBD | Confirms schema and generated data support the planned question patterns. |
| Platform Owner DRI | TBD | Pending | TBD | Confirms Q&A design aligns with v3 evaluation scope. |
| QA/Audit Reviewer | TBD | Pending | TBD | Confirms expected SQL rules are deterministic and auditable. |
