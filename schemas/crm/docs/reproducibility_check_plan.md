# CRM Reproducibility Check Plan

> **Historical note:** written during initial CRM implementation planning (Track A). Any path below referencing the retired `release/crm/<version>/` domain-first layout is stale — the canonical layout is now `release/<version>/crm/...` (see `qa_pairs/utils/release_bundle.py` and the root `README.md`). Kept as the historical record of the original validation design, not a living reference; this pattern was not replicated for the other four domains — see `DOCUMENTATION_IMPLEMENTATION_PLAN.md` §6.

Source of truth:

- Seed and fixed date: `config/generation/base.json`
- CRM row targets: `config/generation/crm/schema.json`
- CRM output path: `config/generation/crm/release.json`
- CSV export contract: `config/generation/base.json`
- Release CSVs: `release/crm/dataset-v1.0.0/`

This document describes the reproducibility gate under Step 18 of
`CRM_Engagement_Schema_Implementation_Plan.md`. A clean regeneration from seed
`42` must produce byte-identical CRM CSV files.

## Reproducibility Rules

- Validate CRM config and DDL alignment before running.
- Run only for the `full` release profile.
- Hash the existing exported release CSVs.
- Regenerate CRM tables from the deterministic seed into a temporary
  directory.
- Export regenerated CSVs with the same CSV writer and configured table order.
- Compute SHA-256 hashes for regenerated CSVs.
- Compare expected filenames, byte counts, and SHA-256 hashes table by table.
- Do not modify the release directory.
- Fail if any expected release CSV is missing or any hash differs.
- Compare all six engagement CRM CSVs in configured dependency order.

## CLI Verification

After full-profile CSV export and dependency installation:

```bash
python main.py validate-reproducibility --profile full
```
