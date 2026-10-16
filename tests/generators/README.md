# Generator Tests

Tests for deterministic dataset generation.

The shared core suite covers:

- Root RNG and child RNG determinism.
- Faker seeding.
- Component-based domain configuration loading.
- CSV export formatting.
- Manifest hashing.
- Row-cap checks.
- Imperfection injection behavior.

The CRM regression suite protects the accepted CRM configuration, generated
tables, validation gates, release artifacts, and deterministic hashes.

The Sales suite covers:

- Descriptor loading, DDL/config/header alignment, and dynamic columns.
- Deterministic base generation and named-stream isolation.
- Sales relationships, ownership rules, chronology, enums, and numeric ranges.
- Pareto, Poisson, and Gaussian-mixture distribution behavior.
- Missing prices, near-duplicate quotation lines, outliers, and boundary dates.
- Immediate stage guards and complete post-generation relational validation.
- Row-cap, DuckDB FK, join-path, and imperfection-rate validation.
- Deterministic CSV export, exact header order, hashes, schema SQL, and the data
  dictionary.
- Manifest immutability and clean-room byte-identical reproducibility.
- Root CLI dispatch for all five dataset-generation domains.

The Finance suite covers the same stage shape as Sales, plus
Finance-specific decimal-policy coverage: `DECIMAL(19,4)` monetary and
`DECIMAL(19,6)` FX-rate semantics, `ROUND_HALF_UP` rounding, and the
transaction-to-FX-rate analytical lookup. See
`tests/generators/finance/README.md`.

The Logistics suite covers configuration/schema alignment, deterministic
base data, distributions, imperfections, relational and persisted
validators, CSV export, hashes, data dictionary, schema SQL, manifest,
reproducibility, and ordered dev/full pipelines — including the
domain-specific orphan-vs-NULL `warehouse_id` distinction and the
near-duplicate shipment rate. See `tests/generators/logistics/README.md`.

The Project Management suite covers component configuration and the
dataset pipeline, with `test_generated_tables.py` mutating valid base
tables one contract at a time to prove the immediate guard rejects
structural, relational, semantic, temporal, decimal, domain-value,
cardinality, currency, and clean-stage drift. See
`tests/generators/project_management/README.md`.

Q&A pair generation (`qa-build` and related commands) is dispatched for all
five domains, not CRM-only — see `qa_pairs/tests/` for that suite, which is
separate from this one.
