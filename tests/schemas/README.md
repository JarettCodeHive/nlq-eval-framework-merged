# Schema Tests

Tests for schema artifacts and schema-derived config.

Current contract suites cover CRM, Sales, and Finance. Finance-specific tests
also execute its fixed-point, reporting-currency, unique-key, and one-sided
debit/credit constraints in DuckDB.
