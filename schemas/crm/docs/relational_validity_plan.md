# CRM Relational Validity Plan

> **Historical note:** written during initial CRM implementation planning (Track A). Any path below referencing the retired `release/crm/<version>/` domain-first layout is stale — the canonical layout is now `release/<version>/crm/...` (see `qa_pairs/utils/release_bundle.py` and the root `README.md`). Kept as the historical record of the original validation design, not a living reference; this pattern was not replicated for the other four domains.

Sources of truth:

- DDL: `schemas/crm/crm_ddl.sql`
- Join contract: `schemas/crm/docs/01_join_path_requirements.md`
- Config entry point: `config/generation/crm.json`
- Schema and relationships: `config/generation/crm/schema.json`
- Consistency rules: `config/generation/crm/validation.json`

Relational validation runs after imperfection injection and before export.

## Validation Contract

- Require all six tables and exact configured column order.
- Enforce non-empty, unique simple keys and the unique
  `(contact_id, campaign_id)` bridge key.
- Require every non-empty FK to reference its parent.
- Allow at most one primary campaign attribution per contact.
- Preserve attribution-weight bounds and per-contact sums, accounting for
  controlled missing weights.
- Require attributed interactions to have a matching campaign membership and
  to fall within the campaign window.
- Require populated interaction account IDs to match the contact account.
- Require populated case contacts to belong to the case account.
- Derive SLA deadlines from opening time and priority; enforce resolution
  status and timestamp ordering.
- Keep CRM campaign currency fixed to USD.

Join cardinality and unmatched-row requirements are validated separately by
the join-path gate.

## CLI

```bash
python main.py validate-relations --profile dev
python main.py validate-relations --profile full
```
