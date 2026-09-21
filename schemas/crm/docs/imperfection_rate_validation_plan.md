# CRM Imperfection Rate Validation Plan

Sources of truth:

- Shared rates: `config/generation/base.json`
- CRM targets: `config/generation/crm/generation.json:imperfection_targets`
- Implementation: `generators/crm/imperfection_rates.py`

## Exact Checks

- Appended contact rows equal `ceil(contacts * duplicate_pct / 100)` and each
  appended row has the configured typo-style name and email variation.
- Missing `contact_campaigns.attribution_weight` values equal
  `ceil(contact_campaigns * null_pct / 100)`.
- Engagement points at or above the configured outlier minimum equal
  `ceil(interactions * outlier_pct / 100)` and remain within 50 through 100.
- Every configured boundary appears in `support_cases.opened_at` at midnight.
- Boundary rows retain correctly derived SLA deadlines and valid optional
  resolution timestamps.

Ordinary nullable business states are excluded from exact imperfection rates.

## CLI

```bash
python main.py validate-imperfection-rates --profile dev --generated
python main.py validate-imperfection-rates --profile full
```
