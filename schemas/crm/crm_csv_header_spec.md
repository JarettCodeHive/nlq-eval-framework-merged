# CRM CSV Header Specification

Status: Draft v0.2 - internally validated; external sign-off pending

Sources of truth:

- DDL: `schemas/crm/crm_ddl.sql`
- ERD: `schemas/crm/crm_er.dbml`
- Generation config: `config/generation/crm.json`

This specification freezes the release filenames, headers, and column order.
Any rename, reorder, addition, or removal requires schema review and renewed
sign-off before generation or Q&A authoring continues.

## CSV Contract

- RFC 4180-compatible CSV with a required header row.
- UTF-8 without BOM and LF line endings.
- Lowercase `snake_case` headers in the exact order below.
- Empty field for SQL NULL and lowercase `true`/`false` for booleans.
- ISO `YYYY-MM-DD` dates and `YYYY-MM-DDTHH:MM:SS` timestamps.
- Period decimal separator without thousands separators.
- `full` is the release profile; maximum 250,000 rows per table.

## `accounts.csv`

Primary key: `account_id`

| # | Header | SQL type | Nullable | Key | References |
|---:|---|---|---|---|---|
| 1 | `account_id` | `INTEGER` | No | PK | |
| 2 | `account_name` | `VARCHAR(255)` | No | | |
| 3 | `account_size` | `INTEGER` | Yes | | |
| 4 | `industry` | `VARCHAR(64)` | Yes | | |
| 5 | `region` | `VARCHAR(64)` | Yes | | |
| 6 | `customer_tier` | `VARCHAR(32)` | Yes | | |
| 7 | `is_active` | `BOOLEAN` | No | | |
| 8 | `created_at` | `TIMESTAMP` | No | | |

```csv
account_id,account_name,account_size,industry,region,customer_tier,is_active,created_at
```

## `contacts.csv`

Primary key: `contact_id`

| # | Header | SQL type | Nullable | Key | References |
|---:|---|---|---|---|---|
| 1 | `contact_id` | `INTEGER` | No | PK | |
| 2 | `account_id` | `INTEGER` | Yes | FK | `accounts.account_id` |
| 3 | `first_name` | `VARCHAR(128)` | No | | |
| 4 | `last_name` | `VARCHAR(128)` | No | | |
| 5 | `email` | `VARCHAR(255)` | No | | |
| 6 | `address` | `VARCHAR(255)` | Yes | | |
| 7 | `title` | `VARCHAR(128)` | Yes | | |
| 8 | `is_active` | `BOOLEAN` | No | | |
| 9 | `created_at` | `TIMESTAMP` | No | | |

```csv
contact_id,account_id,first_name,last_name,email,address,title,is_active,created_at
```

## `campaigns.csv`

Primary key: `campaign_id`

| # | Header | SQL type | Nullable | Key | References |
|---:|---|---|---|---|---|
| 1 | `campaign_id` | `INTEGER` | No | PK | |
| 2 | `campaign_name` | `VARCHAR(255)` | No | | |
| 3 | `campaign_type` | `VARCHAR(64)` | No | | |
| 4 | `primary_channel` | `VARCHAR(32)` | No | | |
| 5 | `status` | `VARCHAR(32)` | No | | |
| 6 | `start_date` | `DATE` | No | | |
| 7 | `end_date` | `DATE` | Yes | | |
| 8 | `budget_amount` | `DECIMAL(15,2)` | Yes | | |
| 9 | `currency_code` | `CHAR(3)` | No | | |
| 10 | `created_at` | `TIMESTAMP` | No | | |

```csv
campaign_id,campaign_name,campaign_type,primary_channel,status,start_date,end_date,budget_amount,currency_code,created_at
```

## `contact_campaigns.csv`

Primary key: composite (`contact_id`, `campaign_id`)

| # | Header | SQL type | Nullable | Key | References |
|---:|---|---|---|---|---|
| 1 | `contact_id` | `INTEGER` | No | PK, FK | `contacts.contact_id` |
| 2 | `campaign_id` | `INTEGER` | No | PK, FK | `campaigns.campaign_id` |
| 3 | `member_status` | `VARCHAR(32)` | No | | |
| 4 | `first_touch_at` | `TIMESTAMP` | Yes | | |
| 5 | `last_touch_at` | `TIMESTAMP` | Yes | | |
| 6 | `attribution_weight` | `DECIMAL(5,4)` | Yes | | |
| 7 | `is_primary_attribution` | `BOOLEAN` | No | | |
| 8 | `created_at` | `TIMESTAMP` | No | | |

```csv
contact_id,campaign_id,member_status,first_touch_at,last_touch_at,attribution_weight,is_primary_attribution,created_at
```

## `interactions.csv`

Primary key: `interaction_id`

| # | Header | SQL type | Nullable | Key | References |
|---:|---|---|---|---|---|
| 1 | `interaction_id` | `INTEGER` | No | PK | |
| 2 | `contact_id` | `INTEGER` | No | FK | `contacts.contact_id` |
| 3 | `account_id` | `INTEGER` | Yes | FK | `accounts.account_id` |
| 4 | `campaign_id` | `INTEGER` | Yes | FK | `campaigns.campaign_id` |
| 5 | `engagement_type` | `VARCHAR(64)` | No | | |
| 6 | `channel` | `VARCHAR(32)` | No | | |
| 7 | `direction` | `VARCHAR(16)` | No | | |
| 8 | `engagement_points` | `INTEGER` | No | | |
| 9 | `interaction_at` | `TIMESTAMP` | No | | |
| 10 | `created_at` | `TIMESTAMP` | No | | |

```csv
interaction_id,contact_id,account_id,campaign_id,engagement_type,channel,direction,engagement_points,interaction_at,created_at
```

## `support_cases.csv`

Primary key: `case_id`; unique business key: `case_number`

| # | Header | SQL type | Nullable | Key | References |
|---:|---|---|---|---|---|
| 1 | `case_id` | `INTEGER` | No | PK | |
| 2 | `account_id` | `INTEGER` | No | FK | `accounts.account_id` |
| 3 | `contact_id` | `INTEGER` | Yes | FK | `contacts.contact_id` |
| 4 | `case_number` | `VARCHAR(64)` | No | UNIQUE | |
| 5 | `subject` | `VARCHAR(255)` | No | | |
| 6 | `category` | `VARCHAR(64)` | No | | |
| 7 | `priority` | `VARCHAR(16)` | No | | |
| 8 | `status` | `VARCHAR(32)` | No | | |
| 9 | `opened_at` | `TIMESTAMP` | No | | |
| 10 | `sla_due_at` | `TIMESTAMP` | No | | |
| 11 | `resolved_at` | `TIMESTAMP` | Yes | | |
| 12 | `created_at` | `TIMESTAMP` | No | | |

```csv
case_id,account_id,contact_id,case_number,subject,category,priority,status,opened_at,sla_due_at,resolved_at,created_at
```

## Release File Order

1. `accounts.csv`
2. `contacts.csv`
3. `campaigns.csv`
4. `contact_campaigns.csv`
5. `interactions.csv`
6. `support_cases.csv`

## Validation Record

| Check | Status | Evidence |
|---|---|---|
| DDL and config table/field/type/nullability/default alignment | Validated | `python main.py validate-config --profile full` |
| ERD and config table/field order alignment | Validated | Automated schema-contract test |
| Header and config filename/field order alignment | Validated | Automated schema-contract test |
| Canonical DDL executes in DuckDB | Validated | Config and FK validation suites |

## Sign-Off

| Role | Name | Status | Date | Notes |
|---|---|---|---|---|
| Data Engineering Lead | TBD | Pending | TBD | Confirms DDL/config/header alignment. |
| Platform Owner DRI | TBD | Pending | TBD | Confirms ingestion contract and header names. |
| QA/Audit Reviewer | TBD | Pending | TBD | Confirms release CSV headers match this specification. |
