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
- Root CLI dispatch for Sales while Q&A commands remain CRM-only.
