# Generators

Seeded synthetic data generation code.

`generators/core` owns reusable deterministic configuration, random streams,
distributions, imperfection helpers, CSV export, hashing, integrity results,
schema-contract parsing, and progress reporting. It must remain free of
domain-specific business rules.

Implemented domain packages:

- `generators/crm`: account, contact, campaign, engagement, and support-case
  generation and validation.
- `generators/sales`: lead, deal, product, quotation, and target generation and
  validation.
- `generators/finance`: chart-of-accounts, transaction, double-entry ledger,
  budget, and synthetic FX generation and validation.
- `generators/logistics`: shipment, order, carrier, warehouse, and inventory
  generation and validation.
- `generators/project_management`: project, task, milestone, resource, and
  time-entry generation and validation.

`generators/domain_registry.py` maps root CLI dataset commands to the correct
domain implementation, for all five domains above. Q&A pair generation
(`qa-build` and related commands) is dispatched the same way, per domain, from
`main.py`'s `QA_STAGE`/`QA_VALIDATE`/`QA_GENERATE`/`QA_REPHRASE` tables — it is
no longer CRM-only. The CRM-only exceptions are the review-only seed-fixture
command (`qa-author-fixtures`) and the taxonomy-matrix regeneration script
(`gen_family_matrix.py`), both of which remain CRM-specific tooling, not a
dispatch limitation.

Generator requirements:

- Use a single deterministic root seed.
- Avoid wall-clock reads, network calls, unordered iteration dependence, and
  nondeterministic parallelism.
- Generate all required controlled imperfections.
- Enforce the 250,000-row-per-table hard cap before export.
- Write CSVs with fixed column order and stable formatting.

Expected answers must be verified from exported CSVs, not from intermediate
dataframes created by these generators.

All five domains intentionally model different business questions. CRM
focuses on engagement, campaign attribution, and support SLA behavior. Sales
focuses on pipeline deals, products, quote lines, and quota attainment. Finance
focuses on accounting, FX-adjusted sums, budget versus actual, and rolling
balances. Logistics focuses on shipment routing, carrier performance, and
warehouse/inventory fulfillment. Project Management focuses on task and
milestone status, resource allocation, and logged-time breakdowns. Shared
code must not collapse those domain semantics into one model.

Domain modules follow the same stage boundary:

1. Generate clean deterministic base tables.
2. Apply configured distributions without changing the schema.
3. Inject only declared imperfections.
4. Validate relational and domain invariants.
5. Export deterministic CSVs and release support artifacts.

Finance-specific fixed-point, accounting, and FX rules remain under
`generators/finance`; only reusable deterministic primitives belong in
`generators/core`.
