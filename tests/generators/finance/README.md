# Finance Generator Tests

This package covers Finance-specific configuration, generation, decimal
policy, distributions, imperfections, validation, export, and release
behavior. Current coverage protects component loading, pre-generation
configuration, deterministic base entities (accounts, FX rates,
transactions, ledger entries, budgets), immediate relational guards,
distribution application, controlled imperfection injection, immediate
imperfection-result validation, and the complete final relational and
business-rule gate.

Decimal-policy coverage (`test_decimal_policy.py`) is Finance-specific and
has no equivalent in the other domains' suites: it verifies `DECIMAL(19,4)`
monetary semantics, `DECIMAL(19,6)` FX-rate semantics, `ROUND_HALF_UP`
rounding, and that no binary floating-point value is ever accepted by the
Finance decimal policy — the zero-numeric-tolerance guarantee (§HC-3) is
only as strong as this layer.

CSV coverage includes the explicit dev preview stages, full release
generation, exact headers, platform-neutral formatting, stale-file cleanup,
and manifest immutability. Row-cap coverage validates configured final
expectations, generated tables, and exported CSV record counts, including
the near-duplicate budget expansion. DuckDB coverage executes the canonical
DDL, loads final CSVs under constraints, checks every physical FK with
explicit anti-joins, and confirms the transaction-to-FX-rate lookup
(unique by source currency, target currency, and transaction date) is
exercised as an analytical join, never treated as a physical foreign key.

Join-path coverage executes every configured Finance path, including the
required LEFT JOIN for transactions deliberately posted with no ledger
rows, and the `ledger_entries` many-to-many bridge between transactions and
accounts.

Imperfection-rate coverage independently measures near-duplicate budgets,
transaction outliers, boundary dates, and NULL FX rates in both in-memory
tables and CSV-loaded data — a NULL rate must never be silently replaced
with a current, previous, or default rate anywhere in this chain.

Hashing coverage verifies configured file order, exact raw-byte counts and
SHA-256 digests, release-profile enforcement, and strict CSV inventory
checks. Data-dictionary coverage verifies complete config-derived table and
field documentation, Finance business semantics, the FX conversion formula,
relationships, distributions, imperfections, fixed-date behavior, and
immutable release rules. Schema-SQL coverage verifies the complete Finance
DDL contract, raw-byte and SHA-256 equality with the canonical source,
atomic publication, full-profile enforcement, and manifest immutability.
Manifest coverage verifies complete release metadata and hashes, companion
artifact correctness, all measured validation gates, atomic publication,
and immutable release sealing. Reproducibility coverage compares filenames,
ordered headers, rows, bytes, and SHA-256 values against a clean temporary
regeneration and confirms the accepted release is never modified during
validation. `test_pipeline.py`/`test_end_to_end.py` exercise the full
`build-dataset` sequence end to end rather than one stage in isolation.
