# Generators

Seeded synthetic data generation code.

`generators/core` owns reusable deterministic configuration, random streams,
distributions, imperfection helpers, CSV export, hashing, integrity results,
schema-contract parsing, and progress reporting. It must remain free of
domain-specific business rules.

```
generators/core/
├── base.py              <- the reproducibility contract: one root seed, stable
│                            named child streams, seeded Faker instances, no
│                            wall-clock reads anywhere in the generation path
├── config.py            <- shared contracts for component-based domain configuration
├── schema_contract.py   <- shared parsing helpers for domain schema contracts
├── distributions.py     <- shared statistical distributions (Pareto, Poisson,
│                            Gaussian-mixture, etc.) every domain selects from
├── imperfections.py     <- controlled imperfection helpers (duplicates, NULLs,
│                            outliers, boundary values)
├── temporal.py          <- domain-neutral deterministic date/timestamp helpers
├── fixed_point.py       <- domain-neutral exact fixed-point unit allocation
│                            (splitting a total across rows with no float drift)
├── integrity.py         <- integrity checks for generated relational datasets
├── csv_export.py        <- CSV export helpers for released dataset artifacts
├── manifest.py          <- manifest generation and artifact hashing helpers
└── progress.py          <- console progress reporting (ProgressReporter for a
                             single generator; PipelineReporter for run-pipeline)
```

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

## Adding a sixth domain

`generators/core` is entirely domain-agnostic — nothing in it has ever
needed to change across the four domains added after CRM. Adding another
domain means, outside this folder: a `config/generation/<domain>/` pair
(`generation.json`, `release.json`) and a `schemas/<domain>/` contract
(DDL, CSV header spec, semantics doc) — then, inside this folder:

1. Create `generators/<domain>/` following the same shape every existing
   domain uses: `generator.py` (base entities), `distributions.py`,
   `imperfections.py`, `export.py`, plus `validators/config.py`,
   `validators/relational.py`, `validators/fk_integrity.py`,
   `validators/join_paths.py`, `validators/row_caps.py`,
   `validators/imperfection_rates.py`, `validators/reproducibility.py` —
   see `generators/crm/README.md` for what each one owns.
2. Register the new domain's components in `generators/domain_registry.py`
   (`DATASET_DOMAINS`) the same way the existing five are wired, so
   `main.py`'s `build-dataset` and related commands can find it.
3. Add the new domain's dataset-generation test suite under
   `tests/generators/<domain>/`, following an existing domain's shape.

Q&A pair generation for the new domain is a separate step, documented in
`qa_pairs/generator/README.md#adding-a-sixth-domain`.
