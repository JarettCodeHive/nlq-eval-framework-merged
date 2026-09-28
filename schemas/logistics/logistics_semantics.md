# Logistics Semantic Contract

Status: Contract v1.0 - frozen for implementation; external sign-off pending

This document is the canonical source for Logistics orphan, duplicate, date,
decimal, currency, order-status, and inventory semantics. Generation,
validation, manifests, data dictionaries, and reference SQL must implement
these definitions without introducing alternative interpretations.

## Fixed Reference Date

- Source: `config/generation/base.json:reference_today`.
- Contract v1.0 value: `2026-08-01`.
- Generation and validation must never read the wall clock.
- Normal generated business dates must not exceed `reference_today`.
- Configured boundary-date test rows are deliberate exceptions to the normal
  date window, but must still preserve all relative chronology rules.

## Orphaned Orders

`orders.warehouse_id` is an analytical LEFT JOIN key, not a physical FK.

- Controlled NULL rate: `2.5%` of base orders, sourced from
  `config/generation/base.json:imperfections.null_pct`.
- Controlled orphan rate: `1.0%` of base orders.
- NULL and orphan positions must be deterministic, selected without
  replacement, and disjoint.
- Orphans replace a populated warehouse ID; NULLs remain empty CSV fields.
- The deterministic orphan namespace starts at `900000000` and uses
  `900000000 + order_id`. It cannot overlap generated warehouse IDs.
- Orphan injection must not change `order_id` or any `shipments.order_id`.
- Validators must report declared orphans separately from unexpected FK
  failures and separately from NULL warehouse assignments.

The exact expected count is calculated from the base order count using the
shared deterministic percentage-counting rule. Rates are never measured from
the post-imperfection row count.

## Duplicate Shipments

The controlled near-duplicate rate is `1.0%` of base shipments, sourced from
`config/generation/base.json:imperfections.duplicate_pct`.

A near-duplicate shipment must:

- receive a new unique `shipment_id`;
- retain the source `order_id`, `carrier_id`, and `tracking_number` business
  key;
- vary at least one of `ship_date`, `status`, or `shipping_cost`;
- remain chronologically and relationally valid;
- never be a byte-identical row copy.

Reference SQL must distinguish physical shipment rows from business shipment
identity when required, particularly `COUNT(*)` versus
`COUNT(DISTINCT tracking_number)`.

## Shipment Date and Status Rules

- `order_date` is the order's calendar creation date.
- A populated `ship_date` cannot precede its order's `order_date`.
- A populated `delivery_date` requires `ship_date` and cannot precede it.
- `Booked`: `ship_date` and `delivery_date` are NULL.
- `InTransit`: `ship_date` is populated and `delivery_date` is NULL.
- `Delivered`: both `ship_date` and `delivery_date` are populated.
- `Failed` and `Lost`: `ship_date` is populated and `delivery_date` is NULL.
- `orders.created_at` cannot be after the end of its `order_date`.
- `shipments.created_at` cannot be after its first populated shipment event;
  for `Booked` rows it cannot be after `reference_today`.

Boundary-date injection must update the selected order and all dependent
shipment dates together. It must preserve status/date consistency and relative
chronology even when the boundary value is outside the normal date window.

## Order Status and Shipment Evidence

- `Pending` orders may have no shipment.
- `Shipped` orders have at least one shipment with shipment evidence.
- `Delivered` orders have at least one `Delivered` shipment.
- `Cancelled` orders may have no shipment.
- `Returned` orders retain at least one shipment-history row.
- The generated data must retain some orders without shipments for LEFT JOIN
  and missing-relationship questions.

Status is explicit business data. It must not be inferred differently by the
generator, validators, data dictionary, or reference SQL.

## Inventory Snapshot Rules

- `inventory_id` is the physical primary key.
- `(warehouse_id, product_sku)` is the unique logical snapshot key.
- `quantity_on_hand` must be non-negative.
- A populated `reorder_point` must be non-negative.
- Every inventory warehouse must exist in `warehouses`.
- `created_at` must not be after `last_updated_at`.
- Product SKUs are synthetic Logistics identifiers and have no cross-domain FK
  to Sales products.

## Decimal Policy

| Field | Precision | Scale | Constraint |
|---|---:|---:|---|
| `carriers.base_rate` | 10 | 2 | Non-negative when populated |
| `warehouses.utilization_pct` | 5 | 2 | `0.00` through `100.00` |
| `orders.total_amount` | 15 | 2 | Positive |
| `shipments.shipping_cost` | 15 | 2 | Non-negative when populated |

- Python must use `decimal.Decimal` or integer minor units.
- Stored decimal strings retain exactly two fractional digits.
- Every rounding operation uses `ROUND_HALF_UP`.
- Calculations use full fixed-point operands and round once at the final scale.
- Binary floating-point arithmetic must not define contract values.

## Currency Policy

- Logistics Contract v1.0 is USD-only.
- `carriers.currency_code = 'USD'`.
- `orders.currency_code = 'USD'`.
- `shipments.currency_code = 'USD'`.
- The canonical DDL enforces all three fixed values.
- No FX table, live-rate lookup, or currency conversion belongs to Logistics.
- Cross-currency evaluation remains a Finance-domain responsibility.

## Required Configuration Encoding

The Logistics component configuration introduced in the configuration step
must encode, and config validation must require:

- `reference_today_source = config/generation/base.json:reference_today`;
- orphan rate `1.0`, namespace base `900000000`, and the ID formula above;
- shared NULL and duplicate rate sources;
- disjoint NULL/orphan selection;
- duplicate business keys and permitted variation fields;
- shipment status/date rules and order-status evidence mapping;
- coordinated boundary-date targets and dependent updates;
- inventory logical uniqueness and quantity constraints;
- scale `2`, `ROUND_HALF_UP`, calculate-first/round-once behavior;
- USD-only currency and no FX path.

Configuration validation must fail when any of these values is absent or
contradicts this contract.
