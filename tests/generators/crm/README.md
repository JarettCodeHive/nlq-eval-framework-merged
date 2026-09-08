# CRM Generator Tests

This suite verifies the engagement-focused CRM dataset contract across:

- DDL, config, table order, column order, and signed-header alignment.
- Deterministic generation using both `dev` and `full` profile settings.
- Configured domain values, engagement mappings, campaign chronology, and
  support-case SLA behavior.
- PK, FK, composite-key, many-to-many, attribution, and cross-table
  consistency rules.
- Distribution behavior, exact imperfection rates, and boundary timestamps.
- CSV export, row caps, SHA-256 hashes, data dictionary, schema SQL, manifest,
  and clean-room reproducibility.
- Absence of retired opportunity-pipeline tables from the CRM contract.

Run the CRM suite from the repository root:

```bash
python -m pytest tests/generators/common tests/generators/crm tests/schemas/crm
```

The full-profile manifest command is intentionally excluded because it seals
the release directory and must remain the final release action.
