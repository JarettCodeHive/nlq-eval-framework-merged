# CRM Generator

CRM is the reference implementation for the data generation framework.

Expected CRM tables:

- `accounts`
- `contacts`
- `campaigns`
- `contact_campaigns`
- `interactions`
- `support_cases`

The generator builds deterministic base tables, applies campaign-budget,
engagement-frequency, and date distributions, then injects controlled contact
duplicates, attribution-weight NULLs, engagement-point outliers, and
support-case boundary timestamps. Validation covers schema contracts, FKs,
semantic relationships, join paths, imperfection rates, row caps, and
reproducibility.

Distribution algorithms use generic presets from
`config/generation/base.json:distribution_defaults`. CRM selects presets and
declares any domain-only overrides in `config/generation/crm.json:distributions`.
At startup, the shared resolver merges and validates both layers; CRM generation
consumes only the effective values in `GenerationSettings.distributions`.

Current CRM selections are `campaign_budget -> pareto_amount`,
`interaction_frequency -> poisson_frequency`, and
`date_clustering -> gaussian_mixture_dates`. CRM currently applies no parameter
overrides.
