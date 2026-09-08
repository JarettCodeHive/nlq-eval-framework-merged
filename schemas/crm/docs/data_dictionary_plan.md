# CRM Data Dictionary Plan

Source of truth:

- CRM DDL: `schemas/crm/crm_ddl.sql`
- CRM generation config: `config/generation/crm.json`
- Current CRM implementation plan:
  `CRM_Engagement_Schema_Implementation_Plan.md`

This document describes data-dictionary generation under Step 17 of
`CRM_Engagement_Schema_Implementation_Plan.md`.

## Dictionary Rules

- Validate CRM config and DDL alignment before generation.
- Generate the release dictionary only for the `full` profile.
- Write `release/crm/dataset-v1.0.0/data_dictionary.md`.
- Refuse to modify the release directory after `manifest.json` exists.
- Include every CRM table and field in configured table/field order.
- For every field, document type, nullability, key role, FK reference,
  business meaning, generation behavior, and imperfection behavior.
- Include relationship and controlled-imperfection summaries.
- Describe engagement scoring, campaign attribution, support-case SLA
  semantics, the USD-only boundary, and the `contact_campaigns` bridge.

## CLI Verification

```bash
python main.py generate-data-dictionary --profile full
```
