# Logistics CSV Header Spec

Status: Draft v0.1 - pending sign-off

Source of truth:

- ERD: `schemas/logistics/logistics_er.dbml`
- DDL: `schemas/logistics/logistics_ddl.sql`

Purpose:

This document freezes the exact CSV file names, header names, and column order
for the Logistics golden dataset. Generated CSV headers must be byte-identical
to the columns below. Any header rename, reorder, addition, or removal requires
schema review before data generation or Q&A authoring proceeds.

## CSV Contract

- Format: RFC 4180 CSV.
- Encoding: UTF-8.
- Header row: required.
- Column names: lowercase snake_case.
- Column order: exactly as listed in this spec.
- NULL representation: empty CSV field.
- Boolean representation: `true` / `false`.
- Monetary precision: `DECIMAL(15, 2)` for order totals and shipment costs.
- Rate precision: `DECIMAL(10, 2)` for carrier base rates.
- Percentage precision: `DECIMAL(5, 2)` for warehouse utilization.
- Maximum rows per table: `250000`.

## Tables

### `carriers.csv`

Primary key: `carrier_id`

| Ordinal | Header | Type | Nullable | Key | References |
|---:|---|---|---|---|---|
| 1 | `carrier_id` | `INTEGER` | No | PK |  |
| 2 | `carrier_name` | `VARCHAR(255)` | No |  |  |
| 3 | `service_level` | `VARCHAR(32)` | No |  |  |
| 4 | `carrier_type` | `VARCHAR(32)` | Yes |  |  |
| 5 | `base_rate` | `DECIMAL(10, 2)` | Yes |  |  |
| 6 | `currency_code` | `CHAR(3)` | No |  |  |
| 7 | `is_active` | `BOOLEAN` | No |  |  |
| 8 | `created_at` | `TIMESTAMP` | No |  |  |

Header row:

```csv
carrier_id,carrier_name,service_level,carrier_type,base_rate,currency_code,is_active,created_at
```

### `warehouses.csv`

Primary key: `warehouse_id`

| Ordinal | Header | Type | Nullable | Key | References |
|---:|---|---|---|---|---|
| 1 | `warehouse_id` | `INTEGER` | No | PK |  |
| 2 | `warehouse_name` | `VARCHAR(255)` | No |  |  |
| 3 | `region` | `VARCHAR(32)` | Yes |  |  |
| 4 | `country_code` | `CHAR(2)` | Yes |  |  |
| 5 | `capacity_units` | `INTEGER` | Yes |  |  |
| 6 | `utilization_pct` | `DECIMAL(5, 2)` | Yes |  |  |
| 7 | `is_active` | `BOOLEAN` | No |  |  |
| 8 | `created_at` | `TIMESTAMP` | No |  |  |

Header row:

```csv
warehouse_id,warehouse_name,region,country_code,capacity_units,utilization_pct,is_active,created_at
```

### `orders.csv`

Primary key: `order_id`

`orders.warehouse_id` intentionally has no SQL foreign key. It may contain a
valid `warehouses.warehouse_id`, NULL, or a non-existent warehouse ID to support
the declared Logistics orphaned-orders imperfection.

| Ordinal | Header | Type | Nullable | Key | References |
|---:|---|---|---|---|---|
| 1 | `order_id` | `INTEGER` | No | PK |  |
| 2 | `warehouse_id` | `INTEGER` | Yes | Analytical key | `warehouses.warehouse_id` by LEFT JOIN only |
| 3 | `customer_name` | `VARCHAR(255)` | No |  |  |
| 4 | `order_date` | `DATE` | No |  |  |
| 5 | `status` | `VARCHAR(32)` | No |  |  |
| 6 | `order_priority` | `VARCHAR(32)` | No |  |  |
| 7 | `total_amount` | `DECIMAL(15, 2)` | No |  |  |
| 8 | `currency_code` | `CHAR(3)` | No |  |  |
| 9 | `created_at` | `TIMESTAMP` | No |  |  |

Header row:

```csv
order_id,warehouse_id,customer_name,order_date,status,order_priority,total_amount,currency_code,created_at
```

### `shipments.csv`

Primary key: `shipment_id`

Duplicate-shipment imperfections are represented with shared business keys such
as `order_id`, `carrier_id`, and `tracking_number`, while `shipment_id` remains
unique.

| Ordinal | Header | Type | Nullable | Key | References |
|---:|---|---|---|---|---|
| 1 | `shipment_id` | `INTEGER` | No | PK |  |
| 2 | `order_id` | `INTEGER` | No | FK | `orders.order_id` |
| 3 | `carrier_id` | `INTEGER` | No | FK | `carriers.carrier_id` |
| 4 | `tracking_number` | `VARCHAR(128)` | No |  |  |
| 5 | `ship_date` | `DATE` | Yes |  |  |
| 6 | `delivery_date` | `DATE` | Yes |  |  |
| 7 | `status` | `VARCHAR(32)` | No |  |  |
| 8 | `shipping_cost` | `DECIMAL(15, 2)` | Yes |  |  |
| 9 | `currency_code` | `CHAR(3)` | No |  |  |
| 10 | `created_at` | `TIMESTAMP` | No |  |  |

Header row:

```csv
shipment_id,order_id,carrier_id,tracking_number,ship_date,delivery_date,status,shipping_cost,currency_code,created_at
```

### `inventory.csv`

Primary key: `inventory_id`

| Ordinal | Header | Type | Nullable | Key | References |
|---:|---|---|---|---|---|
| 1 | `inventory_id` | `INTEGER` | No | PK |  |
| 2 | `warehouse_id` | `INTEGER` | No | FK | `warehouses.warehouse_id` |
| 3 | `product_sku` | `VARCHAR(64)` | No |  |  |
| 4 | `product_category` | `VARCHAR(64)` | Yes |  |  |
| 5 | `quantity_on_hand` | `INTEGER` | No |  |  |
| 6 | `reorder_point` | `INTEGER` | Yes |  |  |
| 7 | `last_updated_at` | `TIMESTAMP` | No |  |  |
| 8 | `created_at` | `TIMESTAMP` | No |  |  |

Header row:

```csv
inventory_id,warehouse_id,product_sku,product_category,quantity_on_hand,reorder_point,last_updated_at,created_at
```

## Release File Order

The release package must include CSVs in this table order:

1. `carriers.csv`
2. `warehouses.csv`
3. `orders.csv`
4. `shipments.csv`
5. `inventory.csv`

## Sign-Off

| Role | Name | Status | Date | Notes |
|---|---|---|---|---|
| Data Engineering Lead | TBD | Pending | TBD | Confirms DDL/header alignment. |
| Platform Owner DRI | TBD | Pending | TBD | Confirms ingestion contract and header names. |
| QA/Audit Reviewer | TBD | Pending | TBD | Confirms release CSV headers match this spec. |
