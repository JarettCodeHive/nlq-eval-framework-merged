# Finance Schema Tests

These tests freeze the Finance Contract v1.0 artifacts before dataset
generation begins. They verify DDL, DBML, and signed-header alignment; DuckDB
execution; physical relationships; unique analytical keys; fixed-point types;
USD reporting currency; and debit/credit row constraints.

Run with:

```bash
python -m pytest tests/schemas/finance -q
```
