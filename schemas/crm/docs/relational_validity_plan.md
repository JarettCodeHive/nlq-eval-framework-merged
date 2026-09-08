# CRM Relational Validity Plan

Sources of truth:

- DDL: `schemas/crm/crm_ddl.sql`
- Join contract: `schemas/crm/docs/01_join_path_requirements.md`
- Generation config: `config/generation/crm.json`

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
