# CRM Data Dictionary Plan

> **Historical note:** written during initial CRM implementation planning (Track A). Any path below referencing the retired `release/crm/<version>/` domain-first layout is stale — the canonical layout is now `release/<version>/crm/...` (see `qa_pairs/utils/release_bundle.py` and the root `README.md`). Kept as the historical record of the original validation design, not a living reference; this pattern was not replicated for the other four domains.

Source of truth:

- CRM DDL: `schemas/crm/crm_ddl.sql`
- CRM config entry point: `config/generation/crm.json`
- Config sections: `config/generation/crm/*.json`
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
