# Generation Fixed Config

This document implements Step 9 of the CRM Golden Dataset Implementation
Plan. It records the fixed configuration values that generator code must use
instead of reading mutable environment or wall-clock state.

## Shared Fixed Values

Source: `config/generation/base.json`

| Setting | Value | Rule |
|---|---|---|
| `seed` | `42` | Root RNG seed for all deterministic generation. |
| `reference_today` | `2026-08-01` | All relative dates resolve against this constant. |
| `max_rows_per_table` | `250000` | Hard cap enforced before export. |
| `release_profile` | `full` | Only this profile may write versioned release artifacts. |

## Profiles

| Profile | Purpose | Delivery allowed |
|---|---|---|
| `dev` | Fast local validation. | No |
| `full` | Delivery-scale output. | Yes |

CRM uses explicit per-table row targets in `config/generation/crm.json`.
The shared `dev` scale expresses the project intent, but does not override
CRM's explicit dev counts.

## CRM Fixed Values

Source: `config/generation/crm.json`

| Setting | Value |
|---|---|
| `domain` | `crm` |
| `dataset_version` | `dataset-v1.0.0` |
| `schema_source` | `schemas/crm/crm_ddl.sql` |
| `manifest_generated_at` | `2026-09-04T00:00:00` |
| `dev` output path | `tmp/generated/crm/dev` |
| `full` output path | `release/crm/dataset-v1.0.0` |

`manifest_generated_at` is intentionally fixed per dataset version. A
wall-clock manifest timestamp would make clean-room regeneration fail the
byte-identical manifest contract.

The CRM table order is `accounts`, `contacts`, `campaigns`,
`contact_campaigns`, `interactions`, and `support_cases`. CRM is USD-only;
mixed-currency conversion belongs to Finance.

## CSV Format

The CSV format contract is also fixed in `base.json`:

- UTF-8 with no BOM.
- LF line endings.
- Empty field for NULL.
- Lowercase `true` / `false` booleans.
- ISO dates and timestamps.
- Period decimal separator and no thousands separator.

## Step 9 Acceptance

Step 9 is complete when:

- The CRM dataset version is fixed.
- The root seed and `reference_today` are fixed.
- `dev` and `full` profiles are declared.
- Output paths are declared for both profiles.
- Release export is restricted to `full`.
- Manifest timestamp behavior is deterministic.
