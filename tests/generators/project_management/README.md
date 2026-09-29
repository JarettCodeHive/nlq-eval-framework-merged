# Project Management Generator Tests

These tests protect the Project Management component configuration and dataset
pipeline as implementation proceeds. Run them with:

```bash
python -m pytest tests/generators/project_management -q
```

`test_generated_tables.py` mutates valid base tables one contract at a time to
prove the immediate guard rejects structural, relational, semantic, temporal,
decimal, domain-value, cardinality, currency, and clean-stage drift.
