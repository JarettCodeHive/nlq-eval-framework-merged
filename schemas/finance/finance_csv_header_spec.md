# Finance CSV Header Spec

Status: Contract v1.0 - implementation-frozen, pending external sign-off

Source of truth:

- ERD: `schemas/finance/finance_er.dbml`
- DDL: `schemas/finance/finance_ddl.sql`

Purpose:

This document freezes the exact CSV file names, header names, and column order
for the Finance golden dataset. Generated CSV headers must be byte-identical to
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
- Monetary precision: `DECIMAL(19, 4)`.
- FX-rate precision: `DECIMAL(19, 6)`.
- Rounding: `ROUND_HALF_UP`; multiply first and round once to monetary scale 4.
- Reporting currency: `USD` for transaction targets, budgets, and FX targets.
- Source/functional currencies: `USD`, `EUR`, `GBP`, `INR`, `JPY`, `CAD`,
  `AUD`, `CHF`, `SGD`, and `AED`.
- Ledger coverage: 5% of transactions intentionally have no ledger entries.
- Maximum rows per table: `250000`.

## Key and Accounting Constraints

- `accounts.account_number` is unique.
- `(ledger_entries.transaction_id, ledger_entries.line_number)` is unique.
- Exactly one of `debit_amount` and `credit_amount` is populated per ledger row,
  and the populated value is positive.
- `(fx_rates.from_currency, fx_rates.to_currency, fx_rates.rate_date)` is unique.
- `transactions.target_currency`, `budgets.currency_code`, and
  `fx_rates.to_currency` are fixed to `USD`.
- Populated monetary values use scale 4; populated FX rates are positive and use
  scale 6.
- The FX relationship is an analytical composite lookup, not a physical foreign
  key.
- Reversals swap debit/credit orientation; rolling-balance calculations must
  not apply an additional reversal multiplier.

## Tables

### `accounts.csv`

Primary key: `account_id`

| Ordinal | Header | Type | Nullable | Key | References |
|---:|---|---|---|---|---|
| 1 | `account_id` | `INTEGER` | No | PK |  |
| 2 | `account_number` | `VARCHAR(64)` | No | UK |  |
| 3 | `account_name` | `VARCHAR(255)` | No |  |  |
| 4 | `account_type` | `VARCHAR(32)` | No |  |  |
| 5 | `account_subtype` | `VARCHAR(64)` | Yes |  |  |
| 6 | `currency_code` | `CHAR(3)` | No |  |  |
| 7 | `parent_account_id` | `INTEGER` | Yes | FK | `accounts.account_id` |
| 8 | `normal_balance` | `VARCHAR(16)` | No |  |  |
| 9 | `is_active` | `BOOLEAN` | No |  |  |
| 10 | `created_at` | `TIMESTAMP` | No |  |  |

Header row:

```csv
account_id,account_number,account_name,account_type,account_subtype,currency_code,parent_account_id,normal_balance,is_active,created_at
```

### `transactions.csv`

Primary key: `transaction_id`

FX-adjusted calculations use `source_currency`, `target_currency`, and
`transaction_date` to look up `fx_rates`. This is an analytical composite
lookup, not a physical foreign key.

| Ordinal | Header | Type | Nullable | Key | References |
|---:|---|---|---|---|---|
| 1 | `transaction_id` | `INTEGER` | No | PK |  |
| 2 | `transaction_date` | `DATE` | No |  | `fx_rates.rate_date` analytical lookup |
| 3 | `description` | `VARCHAR(1024)` | Yes |  |  |
| 4 | `source_system` | `VARCHAR(32)` | No |  |  |
| 5 | `source_currency` | `CHAR(3)` | No | Analytical key | `fx_rates.from_currency` |
| 6 | `target_currency` | `CHAR(3)` | No | Analytical key; fixed value | `fx_rates.to_currency`; `USD` |
| 7 | `total_amount` | `DECIMAL(19, 4)` | No |  |  |
| 8 | `reversed` | `BOOLEAN` | No |  |  |
| 9 | `posted_at` | `TIMESTAMP` | No |  |  |

Header row:

```csv
transaction_id,transaction_date,description,source_system,source_currency,target_currency,total_amount,reversed,posted_at
```

### `ledger_entries.csv`

Primary key: `entry_id`

`ledger_entries` is the explicit many-to-many bridge between `transactions` and
`accounts`, while also carrying double-entry line attributes. The combination
of `transaction_id` and `line_number` is unique. Exactly one of `debit_amount`
or `credit_amount` is populated with a positive value on each row.

| Ordinal | Header | Type | Nullable | Key | References |
|---:|---|---|---|---|---|
| 1 | `entry_id` | `INTEGER` | No | PK |  |
| 2 | `transaction_id` | `INTEGER` | No | FK | `transactions.transaction_id` |
| 3 | `account_id` | `INTEGER` | No | FK | `accounts.account_id` |
| 4 | `line_number` | `INTEGER` | No |  |  |
| 5 | `debit_amount` | `DECIMAL(19, 4)` | Yes |  |  |
| 6 | `credit_amount` | `DECIMAL(19, 4)` | Yes |  |  |
| 7 | `currency_code` | `CHAR(3)` | No | Fixed value | `USD` |
| 8 | `posting_type` | `VARCHAR(32)` | No |  |  |
| 9 | `created_at` | `TIMESTAMP` | No |  |  |

Header row:

```csv
entry_id,transaction_id,account_id,line_number,debit_amount,credit_amount,currency_code,posting_type,created_at
```

### `budgets.csv`

Primary key: `budget_id`

| Ordinal | Header | Type | Nullable | Key | References |
|---:|---|---|---|---|---|
| 1 | `budget_id` | `INTEGER` | No | PK |  |
| 2 | `account_id` | `INTEGER` | No | FK | `accounts.account_id` |
| 3 | `fiscal_year` | `INTEGER` | No |  |  |
| 4 | `period_start` | `DATE` | No |  |  |
| 5 | `period_end` | `DATE` | No |  |  |
| 6 | `budget_amount` | `DECIMAL(19, 4)` | No |  |  |
| 7 | `currency_code` | `CHAR(3)` | No |  |  |
| 8 | `scenario` | `VARCHAR(32)` | No |  |  |
| 9 | `created_at` | `TIMESTAMP` | No |  |  |

Header row:

```csv
budget_id,account_id,fiscal_year,period_start,period_end,budget_amount,currency_code,scenario,created_at
```

### `fx_rates.csv`

Primary key: `rate_id`

`fx_rates` is joined analytically by `(from_currency, to_currency, rate_date)`.
This composite key is unique. `rate` is nullable by design to support
missing-FX-rate imperfection tests; populated rates must be positive.

| Ordinal | Header | Type | Nullable | Key | References |
|---:|---|---|---|---|---|
| 1 | `rate_id` | `INTEGER` | No | PK |  |
| 2 | `from_currency` | `CHAR(3)` | No | Analytical key | `transactions.source_currency` |
| 3 | `to_currency` | `CHAR(3)` | No | Analytical key; fixed value | `transactions.target_currency`; `USD` |
| 4 | `rate_date` | `DATE` | No | Analytical key | `transactions.transaction_date` |
| 5 | `rate` | `DECIMAL(19, 6)` | Yes |  |  |
| 6 | `rate_source` | `VARCHAR(32)` | No |  |  |
| 7 | `is_estimated` | `BOOLEAN` | No |  |  |
| 8 | `created_at` | `TIMESTAMP` | No |  |  |

Header row:

```csv
rate_id,from_currency,to_currency,rate_date,rate,rate_source,is_estimated,created_at
```

## Release File Order

The release package must include CSVs in this table order:

1. `accounts.csv`
2. `transactions.csv`
3. `ledger_entries.csv`
4. `budgets.csv`
5. `fx_rates.csv`

## Sign-Off

| Role | Name | Status | Date | Notes |
|---|---|---|---|---|
| Data Engineering Lead | TBD | Pending | TBD | Confirms DDL/header alignment. |
| Platform Owner DRI | TBD | Pending | TBD | Confirms ingestion contract and header names. |
| QA/Audit Reviewer | TBD | Pending | TBD | Confirms release CSV headers match this spec. |
