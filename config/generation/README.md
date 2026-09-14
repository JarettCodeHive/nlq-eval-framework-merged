# Generation Config

Declarative settings for deterministic synthetic dataset generation.

Current files:

- `base.json`: shared seed, fixed date, profiles, row cap, CSV format, reusable
  distribution presets under `distribution_defaults`, imperfection percentages,
  and boundary dates. Presets are defaults, not mandatory cross-domain values.
- `crm.json`: stable CRM entry-point descriptor.
- `crm/release.json`: CRM identity, fixed references, paths, table order, and
  release rules.
- `crm/schema.json`: CRM table, field, row-target, key, and relationship
  contract.
- `crm/generation.json`: CRM domain values, generation rules, business
  mappings, distribution selections and targets, and imperfection targets.
- `crm/validation.json`: CRM join-path and consistency requirements.

Each domain distribution selects a shared preset by name. Any additional keys
in that domain setting override the selected preset. The framework validates
and merges both layers before generation, then exposes the result through
`GenerationSettings.distributions`.

For example, CRM currently selects:

| CRM setting | Shared preset | CRM overrides |
|---|---|---|
| `campaign_budget` | `pareto_amount` | None |
| `interaction_frequency` | `poisson_frequency` | None |
| `date_clustering` | `gaussian_mixture_dates` | None |

To tune only CRM, add an algorithm-compatible parameter beside `preset` in
`crm/generation.json`. Do not change `base.json` unless the reusable default
itself should change for every domain that inherits it.

The `crm.json` descriptor is the logical source and the component files own its
sections; `base.json` is the source for reusable defaults. The shared loader
assembles one runtime dictionary before Python validation checks structure, DDL
alignment, keys, relationships, references, and value shape. Generator modules
do not read individual component files or maintain a second hardcoded copy of
CRM tables, enums, join paths, or effective distribution parameters.

The `dev` profile is for local validation. The `full` profile is the only
profile permitted to write a versioned release.
