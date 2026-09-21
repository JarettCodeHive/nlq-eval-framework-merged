# Sales Generator Tests

This package covers Sales-specific configuration, generation, distributions,
imperfections, validation, export, and release behavior as those stages are
implemented. Current coverage protects component loading, pre-generation
configuration, deterministic base entities, immediate relational guards,
distribution application, distribution-specific validation, controlled
imperfection injection, immediate imperfection-result validation, and the
complete final relational and business-rule gate. CSV coverage includes the
three explicit dev preview stages, full release generation, exact headers,
platform-neutral formatting, stale-file cleanup, and manifest immutability.
Row-cap coverage validates configured final expectations, generated tables,
and exported CSV record counts, including quotation duplicate expansion.
DuckDB coverage executes the canonical DDL, loads final CSVs under constraints,
checks every physical FK with explicit anti-joins, and confirms the analytical
representative relationship is not treated as a foreign key.
Join-path coverage executes all eight configured Sales paths, verifies required
unmatched parents for lead conversion and quota attainment, and proves that the
quotations bridge creates Deal-to-Product many-to-many cardinality in both
directions.
Imperfection-rate coverage independently measures quotation near-duplicates,
missing product prices, deal-amount outliers, and all configured product
boundary timestamps in both in-memory tables and CSV-loaded data.
Hashing coverage verifies configured file order, exact raw-byte counts and
SHA-256 digests, release-profile enforcement, and strict CSV inventory checks.
Data-dictionary coverage verifies complete config-derived table and field
documentation, Sales business semantics, formulas, relationships,
distributions, imperfections, fixed-date behavior, and immutable release rules.
Schema-SQL coverage verifies the complete Sales DDL contract, raw-byte and
SHA-256 equality with the canonical source, atomic publication, full-profile
enforcement, and manifest immutability.
Manifest coverage verifies complete release metadata and hashes, companion
artifact correctness, all four measured validation gates, atomic publication,
and immutable release sealing.
Reproducibility coverage compares filenames, ordered headers, rows, bytes, and
SHA-256 values against a clean temporary regeneration and confirms the accepted
release is never modified during validation.
