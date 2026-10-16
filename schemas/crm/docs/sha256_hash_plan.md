# CRM SHA-256 Hash Plan

> **Historical note:** written during initial CRM implementation planning (Track A). Any path below referencing the retired `release/crm/<version>/` domain-first layout is stale — the canonical layout is now `release/<version>/crm/...` (see `qa_pairs/utils/release_bundle.py` and the root `README.md`). Kept as the historical record of the original validation design, not a living reference; this pattern was not replicated for the other four domains.

Source of truth:

- Exported CSV path: `release/crm/dataset-v1.0.0/`
- Table order: `config/generation/crm/release.json`
- CSV format contract: `config/generation/base.json`

This document describes release hashing under Step 18 of
`CRM_Engagement_Schema_Implementation_Plan.md`.

The expected files are `accounts.csv`, `contacts.csv`, `campaigns.csv`,
`contact_campaigns.csv`, `interactions.csv`, and `support_cases.csv`.

## Hashing Rules

- Validate CRM config and DDL alignment before hashing.
- Hash only the exported release CSV files for the `full` profile.
- Read files as bytes and compute SHA-256 without loading entire files into
  memory.
- Preserve CRM table order in the reported hash list.
- Do not regenerate CSVs during hashing.
- Do not write `manifest.json`; manifest generation remains the final command.

## CLI Verification

After exporting full-profile CSVs:

```bash
python main.py compute-sha256 --profile full
```

The command fails if an expected CRM release CSV is missing or if the release
directory contains a CSV outside the configured six-table contract.
