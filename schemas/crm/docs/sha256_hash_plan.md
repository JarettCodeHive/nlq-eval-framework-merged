# CRM SHA-256 Hash Plan

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
