# CRM Manifest Generation Plan

Source of truth:

- Shared metadata: `config/generation/base.json`
- CRM config entry point: `config/generation/crm.json`
- CRM release and embedded generation metadata:
  `config/generation/crm/release.json` and
  `config/generation/crm/generation.json`
- Exported CSVs: `release/crm/dataset-v1.0.0/`
- Hash rules: `schemas/crm/docs/sha256_hash_plan.md`
- Validation gates: row caps, FK integrity, join paths, and imperfection rates

This document describes manifest generation under Step 18 of
`CRM_Engagement_Schema_Implementation_Plan.md`.

## Manifest Rules

- Generate `manifest.json` only for the `full` profile.
- Refuse to overwrite an existing manifest.
- Validate CRM config and DDL alignment before manifest generation.
- Require all expected exported CSVs to exist.
- Include dataset version, seed, fixed reference date, fixed manifest timestamp,
  schema source, output path, table order, CSV format, and row cap.
- Include each table's role, primary key, configured row target, exported row
  and column metadata, full configured field definitions, byte size, and SHA-256
  hash.
- Include domain values, business mappings, distribution and imperfection
  parameters and targets, relationships, join-path requirements, consistency
  rules, library versions, and validation status.
- Reject any exported CSV whose header differs from its configured field order.
- Run exported validation gates before writing the manifest.
- Validate exported row caps before sealing the release.
- Fail manifest generation if row caps, FK integrity, join paths, or
  imperfection-rate validation fails.
- Generate the release data dictionary and schema SQL and complete the
  reproducibility check before writing the manifest.
- Treat manifest generation as the final release command. No command that
  writes into the release directory may run afterward.

## CLI Verification

After exporting full-profile CSVs, generating all release documentation and
schema artifacts, and completing every validation and reproducibility check:

```bash
python main.py generate-manifest --profile full
```

The command writes:

```text
release/crm/dataset-v1.0.0/manifest.json
```
