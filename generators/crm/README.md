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

## Module Layout

Generation modules create or transform CRM data:

- `generator.py` builds deterministic base entities.
- `distributions.py` applies configured statistical distributions.
- `imperfections.py` injects controlled imperfections.
- `export.py` writes release CSVs.

Validation modules live under `validators/`:

- `validators/config.py` validates config, schema, DDL, relationships,
  generation rules, and join-path contracts before generation runs.
- `validators/relational.py` validates generated in-memory table structure,
  keys, FK consistency, temporal rules, and CRM-specific semantic rules.
- `validators/fk_integrity.py` validates exported or generated CSVs by loading
  them through DuckDB constraints and manual FK checks.
- `validators/join_paths.py` validates required INNER, LEFT, three-table, and
  many-to-many join paths.
- `validators/row_caps.py` validates configured, generated, and exported row
  counts against the hard table cap.
- `validators/imperfection_rates.py` validates controlled duplicate, NULL,
  outlier, and boundary-value rates.
- `validators/reproducibility.py` validates full-release CSV hashes against a
  clean deterministic regeneration.

Validator modules are imported directly from `generators.crm.validators`.

Distribution algorithms use generic presets from
`config/generation/base.json:distribution_defaults`. CRM selects presets and
declares domain-only overrides in
`config/generation/crm/generation.json:distributions`. The public logical
reference remains `config/generation/crm.json:distributions`, resolved against
the assembled CRM namespace. At startup, the shared loader assembles the CRM
components, then the distribution resolver merges and validates both layers;
CRM generation consumes only the effective values in
`GenerationSettings.distributions`.

Current CRM selections are `campaign_budget -> pareto_amount`,
`interaction_frequency -> poisson_frequency`, and
`date_clustering -> gaussian_mixture_dates`. CRM currently applies no parameter
overrides.
