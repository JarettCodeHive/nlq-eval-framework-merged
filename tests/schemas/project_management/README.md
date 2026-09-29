# Project Management Schema Tests

These tests freeze the Project Management Contract v1.0 artifacts before
dataset generation begins. They verify exact DDL, DBML, and CSV-header
alignment; DuckDB execution; table order; primary and foreign keys; the
`task_resources` many-to-many bridge; release file order; and the frozen date,
decimal, milestone, and USD-only semantic contract.

Run with:

```bash
python -m pytest tests/schemas/project_management -q
```
