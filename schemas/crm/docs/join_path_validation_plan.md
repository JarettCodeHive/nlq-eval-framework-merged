# CRM Join Path Validation Plan

> **Historical note:** written during initial CRM implementation planning (Track A). Any path below referencing the retired `release/crm/<version>/` domain-first layout is stale — the canonical layout is now `release/<version>/crm/...` (see `qa_pairs/utils/release_bundle.py` and the root `README.md`). Kept as the historical record of the original validation design, not a living reference; this pattern was not replicated for the other four domains — see `DOCUMENTATION_IMPLEMENTATION_PLAN.md` §6.

Sources of truth:

- Join specification: `schemas/crm/docs/01_join_path_requirements.md`
- Config entry point: `config/generation/crm.json`
- Executable contract: `config/generation/crm/validation.json`
- DDL: `schemas/crm/crm_ddl.sql`

The DuckDB validator loads all six CSVs under the canonical DDL and evaluates
the nine stable paths `crm_jp_001` through `crm_jp_009`.

## Rules

- Every INNER JOIN path must return rows.
- Every LEFT JOIN path must return rows and demonstrate unmatched driving rows.
- Three-table paths cover account engagement, campaign participation, and
  account/case/requester analysis.
- The `contact_campaigns` bridge must contain contacts linked to multiple
  campaigns and campaigns linked to multiple contacts.
- Generated validation uses temporary CSVs; exported validation reads the
  release files without regeneration.

## CLI

```bash
python main.py validate-join-paths --profile dev --generated
python main.py validate-join-paths --profile full
```
