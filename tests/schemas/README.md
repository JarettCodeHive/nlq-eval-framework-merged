# Schema Tests

Tests for schema artifacts and schema-derived config.

Current contract suites cover CRM, Sales, Finance, and Project Management.
Finance-specific tests also execute its fixed-point, reporting-currency,
unique-key, and one-sided debit/credit constraints in DuckDB.

**Logistics has no schema contract test suite** — unlike the other four
domains, there is no `tests/schemas/logistics/` package verifying that
`schemas/logistics/logistics_ddl.sql`, `logistics_csv_header_spec.md`,
`logistics_semantics.md`, and `config/generation/logistics.json` stay
aligned. This is a genuine test gap, not a missing README. Logistics *is*
covered elsewhere —
`tests/generators/logistics/` validates the generated/exported data against
the schema at runtime — but the static contract-alignment check every
other domain has does not exist for this one.
