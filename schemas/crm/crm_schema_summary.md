# CRM Schema Summary

## Status

This document summarizes the CRM ERD in `schemas/crm/crm_er.dbml`. The schema
contract is Draft v0.2, internally validated, and pending external sign-off.
It reflects the client feedback separating CRM from Sales and is aligned with
the executable source of truth, `schemas/crm/crm_ddl.sql`.

The ERD, DDL, `config/generation/crm.json`, and
`schemas/crm/crm_csv_header_spec.md` agree on all six tables and field order.
The DDL and config also agree on physical data types, nullability, defaults,
primary keys, the unique case number, and foreign-key relationships. Business
rules that require comparisons across rows remain generator and validator
responsibilities.

## Domain Scope

The revised CRM schema focuses on:

- Customer and contact engagement scoring.
- Support-case resolution and SLA compliance.
- Campaign participation and attribution.
- Customer relationship and account-level analysis.

Sales owns leads, deals, pipeline stages, products, quotations, revenue, sales
representatives, and quota attainment. CRM has no cross-domain foreign keys to
Sales.

CRM is a USD-only dataset. `campaigns.currency_code` must always be `USD`.
Mixed-currency aggregation and FX conversion remain part of the Finance domain.

## Executable Constraints

The canonical DDL enforces:

- Primary keys on every entity or fact table.
- Composite primary key `(contact_id, campaign_id)` on `contact_campaigns`.
- Unique `support_cases.case_number` values.
- Required and optional foreign keys with the nullability shown in the ERD.
- `campaigns.currency_code = 'USD'`.
- Campaign `end_date` on or after `start_date` when populated.
- Attribution weights from `0.0000` through `1.0000` when populated.
- `last_touch_at >= first_touch_at` when both timestamps are populated.
- Non-negative `interactions.engagement_points`.
- SLA due and resolved timestamps on or after the case opening timestamp.

Same-account consistency, attributed campaign membership, one primary
attribution per contact, status-dependent timestamps, and true many-to-many
coverage cannot be fully represented by single-row DDL constraints. They are
enforced by generation and integrity validation.

## Relationship Overview

```text
accounts
  |-- contacts
  |     |-- interactions
  |     |-- support_cases
  |     `-- contact_campaigns -- campaigns
  |-- interactions
  `-- support_cases
```

`contact_campaigns` is the explicit many-to-many junction between contacts and
campaigns. Its composite primary key is `(contact_id, campaign_id)`.

## Tables

### `accounts`

Customer-organization dimension. Account engagement and relationship health
are derived from interactions and support cases rather than stored directly.

| Field | Purpose |
|---|---|
| `account_id` | Unique account identifier and primary key. |
| `account_name` | Synthetic customer organization name. |
| `account_size` | Synthetic measure of company size. |
| `industry` | Customer industry classification. |
| `region` | Geographic or operational region. |
| `customer_tier` | Customer tier: Standard, Preferred, or Strategic. |
| `is_active` | Indicates whether the account is active. |
| `created_at` | Account creation timestamp. |

### `contacts`

Customer-contact dimension. A contact may optionally belong to an account, and
some accounts deliberately have no contacts for LEFT JOIN coverage.

| Field | Purpose |
|---|---|
| `contact_id` | Unique contact identifier and primary key. |
| `account_id` | Nullable foreign key to `accounts.account_id`. |
| `first_name` | Synthetic contact first name. |
| `last_name` | Synthetic contact last name. |
| `email` | Synthetic contact email address. |
| `address` | Synthetic single-line contact address. |
| `title` | Contact job title. |
| `is_active` | Indicates whether the contact is active. |
| `created_at` | Contact creation timestamp. |

Near-duplicate contacts remain a controlled imperfection. Contact engagement
score is derived from related interaction points rather than stored on this
table.

### `campaigns`

Campaign dimension for customer awareness, nurture, retention, re-engagement,
education, participation, and attribution analysis.

| Field | Purpose |
|---|---|
| `campaign_id` | Unique campaign identifier and primary key. |
| `campaign_name` | Synthetic campaign name. |
| `campaign_type` | Awareness, Nurture, Retention, Reengagement, or CustomerEducation. |
| `primary_channel` | Main channel: Email, Phone, Web, Webinar, Event, or Social. |
| `status` | Draft, Scheduled, Active, Completed, or Cancelled. |
| `start_date` | Campaign start date. |
| `end_date` | Nullable end date for active or open-ended campaigns. |
| `budget_amount` | Nullable campaign budget denominated in USD. |
| `currency_code` | Fixed value `USD`; CRM performs no FX conversion. |
| `created_at` | Campaign creation timestamp. |

Campaign results are evaluated through contact membership and attributed
interactions, not through sales deals or revenue.

### `contact_campaigns`

Explicit many-to-many junction between contacts and campaigns. Neither foreign
key is unique by itself; their combination is the primary key.

| Field | Purpose |
|---|---|
| `contact_id` | Required foreign key to `contacts.contact_id`. |
| `campaign_id` | Required foreign key to `campaigns.campaign_id`. |
| `member_status` | Targeted, Sent, Engaged, Responded, Converted, or OptedOut. |
| `first_touch_at` | Timestamp of the contact's first campaign touch. |
| `last_touch_at` | Timestamp of the contact's most recent campaign touch. |
| `attribution_weight` | Nullable multi-touch attribution weight from 0.0000 to 1.0000. |
| `is_primary_attribution` | Identifies the primary attributed campaign when one is assigned. |
| `created_at` | Campaign-membership creation timestamp. |

### `interactions`

Customer engagement fact table and the source for contact, account, campaign,
channel, and period-level engagement scoring.

| Field | Purpose |
|---|---|
| `interaction_id` | Unique engagement-event identifier and primary key. |
| `contact_id` | Required foreign key to `contacts.contact_id`. |
| `account_id` | Nullable account reference; must agree with the contact account when populated. |
| `campaign_id` | Nullable campaign reference; NULL means organic or unattributed engagement. |
| `engagement_type` | EmailOpen, LinkClick, FormSubmission, PhoneCall, Meeting, WebinarAttendance, or EventAttendance. |
| `channel` | Email, Phone, Web, Webinar, Event, or Social. |
| `direction` | Inbound or Outbound interaction direction. |
| `engagement_points` | Deterministic event-level points used to derive engagement scores. |
| `interaction_at` | Timestamp when the customer interaction occurred. |
| `created_at` | Interaction record creation timestamp. |

The overall engagement score is calculated rather than stored:

```sql
SUM(interactions.engagement_points)
```

### `support_cases`

Customer-service fact table for case volume, backlog, resolution-duration, and
SLA-compliance analysis.

| Field | Purpose |
|---|---|
| `case_id` | Unique support-case identifier and primary key. |
| `account_id` | Required foreign key to `accounts.account_id`. |
| `contact_id` | Nullable requesting contact; supports account-level cases. |
| `case_number` | Unique synthetic business-facing case reference. |
| `subject` | Synthetic short summary of the issue or service request. |
| `category` | ProductIssue, Billing, Access, Integration, ServiceRequest, or GeneralInquiry. |
| `priority` | Low, Medium, High, or Critical. |
| `status` | New, InProgress, PendingCustomer, Resolved, or Closed. |
| `opened_at` | Timestamp when the case was opened. |
| `sla_due_at` | Resolution deadline used for SLA calculations. |
| `resolved_at` | Nullable resolution timestamp for resolved or closed cases. |
| `created_at` | Support-case record creation timestamp. |

## Derived Metrics

| Metric | Definition |
|---|---|
| Contact engagement score | `SUM(interactions.engagement_points)` grouped by contact. |
| Account engagement score | `SUM(interactions.engagement_points)` grouped by account. |
| Campaign engagement score | `SUM(interactions.engagement_points)` grouped by attributed campaign. |
| Resolved within SLA | `resolved_at <= sla_due_at`. |
| Resolved SLA breach | `resolved_at > sla_due_at`. |
| Open SLA breach | `resolved_at IS NULL AND fixed_today > sla_due_at`. |
| Resolution duration | `resolved_at - opened_at`. |

Derived metrics are intentionally not stored as shortcut fields. This preserves
the joins, aggregations, NULL handling, and date logic needed for NLQ evaluation.

## Join Semantics

Required INNER JOIN paths must produce non-empty results:

- `support_cases.account_id -> accounts.account_id`
- `interactions.contact_id -> contacts.contact_id`
- `contact_campaigns.contact_id -> contacts.contact_id`
- `contact_campaigns.campaign_id -> campaigns.campaign_id`

Required LEFT JOIN scenarios must include unmatched rows:

- Accounts without contacts.
- Contacts without interactions.
- Contacts without support cases.
- Campaigns without attributed interactions.
- Organic or unattributed interactions with NULL `campaign_id`.

## Controlled Imperfections

| Target | Behavior |
|---|---|
| `contacts` | Near-duplicate rows at approximately 1%. |
| `contact_campaigns.attribution_weight` | NULL values at exactly the configured 2.5% rate. |
| `interactions.engagement_points` | High-value outliers at approximately 0.5%. |
| Support-case SLA timestamps | Deterministic boundary values. |
| Nullable foreign keys | Empty values allowed, but every populated value must reference a valid parent row. |
