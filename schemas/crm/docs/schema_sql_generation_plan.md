# CRM Schema SQL Generation Plan

Source of truth:

- Canonical CRM DDL: `schemas/crm/crm_ddl.sql`
- CRM config entry point: `config/generation/crm.json`
- CRM schema contract: `config/generation/crm/schema.json`

This document describes schema-copy generation under Step 18 of
`CRM_Engagement_Schema_Implementation_Plan.md`.

The canonical schema contains the six engagement CRM tables: `accounts`,
`contacts`, `campaigns`, `contact_campaigns`, `interactions`, and
`support_cases`.

## Schema SQL Rules

- Validate CRM config and DDL alignment before generation.
- Generate `schema.sql` only for the `full` profile.
- Copy `schemas/crm/crm_ddl.sql` into
  `release/crm/dataset-v1.0.0/schema.sql`.
- Preserve the canonical DDL text exactly.
- Refuse to modify the release directory after `manifest.json` exists.
- Do not infer schema from CSVs; `schemas/crm/crm_ddl.sql` remains the source
  of truth.

## CLI Verification

```bash
python main.py generate-schema-sql --profile full
```
