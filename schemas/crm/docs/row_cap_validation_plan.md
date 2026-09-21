# CRM Row-Cap Validation Plan

Sources of truth:

- Cap: `config/generation/base.json:max_rows_per_table`
- Row targets: `config/generation/crm/schema.json`
- Duplicate rate: `config/generation/base.json:imperfections.duplicate_pct`

The hard cap is 250,000 rows per table. Expected and actual counts include the
near-duplicate contact rows.

| Table | Final full rows | Status |
|---|---:|---|
| `accounts` | 24,000 | Pass |
| `contacts` | 48,480 | Pass |
| `campaigns` | 5,000 | Pass |
| `contact_campaigns` | 180,000 | Pass |
| `interactions` | 200,000 | Pass |
| `support_cases` | 144,000 | Pass |

## Validation Modes

```bash
# Configured expected counts; does not generate or read CSVs
python main.py validate-row-caps --profile full

# Freshly generated in-memory tables
python main.py validate-row-caps --profile full --generated

# Existing release CSV records
python main.py validate-row-caps --profile full --exported
```

`--generated` and `--exported` are mutually exclusive.
