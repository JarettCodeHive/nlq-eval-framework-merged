# Sales Schema Tests

These tests keep `sales_ddl.sql`, `sales_er.dbml`, and
`sales_csv_header_spec.md` aligned before Sales generation is implemented.
They also execute the canonical DDL in DuckDB and protect the USD-only and
analytical quota-join contracts.
