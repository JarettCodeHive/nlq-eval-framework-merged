# Sales CSV Header Spec

Status: Draft v0.1 - pending sign-off

Source of truth:

- ERD: `schemas/sales/sales_er.dbml`
- DDL: `schemas/sales/sales_ddl.sql`

Purpose:

This document freezes the exact CSV file names, header names, and column order
for the Sales golden dataset. Generated CSV headers must be byte-identical to
the columns below. Any header rename, reorder, addition, or removal requires
schema review before data generation or Q&A authoring proceeds.

## CSV Contract

- Format: RFC 4180 CSV.
- Encoding: UTF-8.
- Header row: required.
- Column names: lowercase snake_case.
- Column order: exactly as listed in this spec.
- NULL representation: empty CSV field.
- Boolean representation: `true` / `false`.
- Maximum rows per table: `250000`.

## Tables

### `leads.csv`

Primary key: `lead_id`

| Ordinal | Header | Type | Nullable | Key | References |
|---:|---|---|---|---|---|
| 1 | `lead_id` | `INTEGER` | No | PK |  |
| 2 | `lead_name` | `VARCHAR(255)` | No |  |  |
| 3 | `company_name` | `VARCHAR(255)` | No |  |  |
| 4 | `lead_source` | `VARCHAR(32)` | No |  |  |
| 5 | `lead_status` | `VARCHAR(32)` | No |  |  |
| 6 | `rep_name` | `VARCHAR(255)` | No |  |  |
| 7 | `territory` | `VARCHAR(64)` | Yes |  |  |
| 8 | `score` | `INTEGER` | Yes |  |  |
| 9 | `created_at` | `TIMESTAMP` | No |  |  |

Header row:

```csv
lead_id,lead_name,company_name,lead_source,lead_status,rep_name,territory,score,created_at
```

### `deals.csv`

Primary key: `deal_id`

| Ordinal | Header | Type | Nullable | Key | References |
|---:|---|---|---|---|---|
| 1 | `deal_id` | `INTEGER` | No | PK |  |
| 2 | `lead_id` | `INTEGER` | No | FK | `leads.lead_id` |
| 3 | `deal_name` | `VARCHAR(255)` | No |  |  |
| 4 | `rep_name` | `VARCHAR(255)` | No | Analytical key | `targets.rep_name` |
| 5 | `stage` | `VARCHAR(32)` | No |  |  |
| 6 | `deal_amount` | `DECIMAL(15, 2)` | No |  |  |
| 7 | `currency_code` | `CHAR(3)` | No |  |  |
| 8 | `close_date` | `DATE` | Yes |  |  |
| 9 | `expected_close_date` | `DATE` | Yes |  |  |
| 10 | `created_at` | `TIMESTAMP` | No |  |  |

Header row:

```csv
deal_id,lead_id,deal_name,rep_name,stage,deal_amount,currency_code,close_date,expected_close_date,created_at
```

### `products.csv`

Primary key: `product_id`

| Ordinal | Header | Type | Nullable | Key | References |
|---:|---|---|---|---|---|
| 1 | `product_id` | `INTEGER` | No | PK |  |
| 2 | `sku` | `VARCHAR(64)` | No |  |  |
| 3 | `product_name` | `VARCHAR(255)` | No |  |  |
| 4 | `category` | `VARCHAR(64)` | Yes |  |  |
| 5 | `list_price` | `DECIMAL(15, 2)` | Yes |  |  |
| 6 | `currency_code` | `CHAR(3)` | No |  |  |
| 7 | `is_active` | `BOOLEAN` | No |  |  |
| 8 | `created_at` | `TIMESTAMP` | No |  |  |

Header row:

```csv
product_id,sku,product_name,category,list_price,currency_code,is_active,created_at
```

### `quotations.csv`

Primary key: `quotation_id`

`quotations` is the explicit many-to-many bridge between `deals` and
`products`, while also carrying quote line-item attributes.

| Ordinal | Header | Type | Nullable | Key | References |
|---:|---|---|---|---|---|
| 1 | `quotation_id` | `INTEGER` | No | PK |  |
| 2 | `deal_id` | `INTEGER` | No | FK | `deals.deal_id` |
| 3 | `product_id` | `INTEGER` | No | FK | `products.product_id` |
| 4 | `quote_number` | `VARCHAR(64)` | No |  |  |
| 5 | `quantity` | `INTEGER` | No |  |  |
| 6 | `unit_price` | `DECIMAL(15, 2)` | No |  |  |
| 7 | `discount_pct` | `DECIMAL(5, 2)` | No |  |  |
| 8 | `quote_status` | `VARCHAR(32)` | No |  |  |
| 9 | `quoted_at` | `TIMESTAMP` | No |  |  |
| 10 | `created_at` | `TIMESTAMP` | No |  |  |

Header row:

```csv
quotation_id,deal_id,product_id,quote_number,quantity,unit_price,discount_pct,quote_status,quoted_at,created_at
```

### `targets.csv`

Primary key: `target_id`

Quota attainment joins `targets` to `deals` by `rep_name` and a close-date
period predicate. This relationship is analytical and is not enforced as a
foreign key because each rep can have multiple target periods.

| Ordinal | Header | Type | Nullable | Key | References |
|---:|---|---|---|---|---|
| 1 | `target_id` | `INTEGER` | No | PK |  |
| 2 | `rep_name` | `VARCHAR(255)` | No | Analytical key | `deals.rep_name` |
| 3 | `territory` | `VARCHAR(64)` | Yes |  |  |
| 4 | `period_start` | `DATE` | No |  |  |
| 5 | `period_end` | `DATE` | No |  |  |
| 6 | `quota_amount` | `DECIMAL(15, 2)` | No |  |  |
| 7 | `currency_code` | `CHAR(3)` | No |  |  |
| 8 | `created_at` | `TIMESTAMP` | No |  |  |

Header row:

```csv
target_id,rep_name,territory,period_start,period_end,quota_amount,currency_code,created_at
```

## Release File Order

The release package must include CSVs in this table order:

1. `leads.csv`
2. `deals.csv`
3. `products.csv`
4. `quotations.csv`
5. `targets.csv`

## Sign-Off

| Role | Name | Status | Date | Notes |
|---|---|---|---|---|
| Data Engineering Lead | TBD | Pending | TBD | Confirms DDL/header alignment. |
| Platform Owner DRI | TBD | Pending | TBD | Confirms ingestion contract and header names. |
| QA/Audit Reviewer | TBD | Pending | TBD | Confirms release CSV headers match this spec. |
