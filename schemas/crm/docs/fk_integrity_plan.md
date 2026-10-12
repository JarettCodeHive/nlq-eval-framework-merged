# CRM FK Integrity Validation Plan

> **Historical note:** written during initial CRM implementation planning (Track A). Any path below referencing the retired `release/crm/<version>/` domain-first layout is stale — the canonical layout is now `release/<version>/crm/...` (see `qa_pairs/utils/release_bundle.py` and the root `README.md`). Kept as the historical record of the original validation design, not a living reference; this pattern was not replicated for the other four domains — see `DOCUMENTATION_IMPLEMENTATION_PLAN.md` §6.

Source of truth:

- DDL with PK/FK constraints: `schemas/crm/crm_ddl.sql`
- Exported CSV path: `release/crm/dataset-v1.0.0/`
- CSV format contract: `config/generation/base.json`

This document describes the DuckDB FK gate updated in Step 11 of
`CRM_Engagement_Schema_Implementation_Plan.md`.

For the current engagement CRM implementation, the constrained load covers
`accounts`, `contacts`, `campaigns`, `contact_campaigns`, `interactions`, and
`support_cases` in dependency order.

## Validation Rules

- Load the v3 CRM DDL into an in-memory DuckDB database.
- Load exported CSVs into the constrained tables in dependency order.
- Empty CSV fields are treated as SQL `NULL`.
- Nullable FK fields may be `NULL`.
- Required FK fields must load successfully into NOT NULL constrained columns.
- Run explicit anti-join FK checks for every declared FK path.
- Validate both `contact_campaigns` foreign keys and its composite primary key.
- Validate interaction contact/account and campaign-membership consistency.
- Validate support-case contact/account consistency.
- Any load failure or FK violation fails the validation gate.

## CLI Verification

Validate already-exported full-profile CSVs:

```bash
python main.py validate-fk --profile full
```

Validate generated tables through temporary CSVs after installing dependencies:

```bash
python main.py validate-fk --profile dev --generated
```

The generated path does not consume preview CSVs from disk.
