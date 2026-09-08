# CRM Distribution Plan

Sources of truth:

- Reusable presets: `config/generation/base.json:distribution_defaults`
- CRM preset selections, overrides, targets, and mappings:
  `config/generation/crm.json`
- Effective runtime parameters: `GenerationSettings.distributions`
- Canonical DDL: `schemas/crm/crm_ddl.sql`

Distributions are applied after base generation and before imperfections.
The shared presets are overridable defaults. CRM currently selects
`pareto_amount`, `poisson_frequency`, and `gaussian_mixture_dates` without
overrides, so the effective values shown below remain unchanged.

## Distribution Contract

| Target | Distribution | Purpose |
|---|---|---|
| `campaigns.budget_amount` | Clipped Pareto, `alpha=1.16` | Realistic long-tail campaign budgets while preserving configured NULLs and USD-only values. |
| Interactions per contact | Poisson-derived weights, `lambda=3.2` | Uneven engagement frequency across contacts. |
| Campaign attribution | Configured organic share and membership-weighted campaign selection | Keeps attributed interactions on valid contact/campaign memberships. |
| Campaign, membership, interaction, and case timestamps | Three-component Gaussian mixture | Creates deterministic temporal clusters across the configured date ranges. |
| Support-case resolution duration | Configured status/priority behavior | Derives SLA deadlines and valid resolved timestamps from `opened_at`. |

CRM distribution targets refer to a domain `settings_key`; they do not point
directly to shared config parameters. Resolution follows this order:

1. Load the selected shared preset.
2. Apply algorithm-compatible values declared beside `preset` in CRM config.
3. Validate the fully resolved algorithm settings.
4. Apply those effective settings to the configured target fields.

Distribution application preserves table shape, PK/FK values, the
`contact_campaigns` composite key, contact/account consistency, campaign
windows, and support-case status/SLA rules.

## CLI

```bash
python main.py apply-distributions --profile dev
python main.py apply-distributions --profile dev --write-preview
```

Preview CSVs are written to `tmp/generated/crm/dev/distributed/`.
