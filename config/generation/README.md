# Generation Config

Declarative settings for deterministic synthetic dataset generation.

Current files:

- `base.json`: shared seed, fixed date, profiles, row cap, CSV format, reusable
  distribution presets under `distribution_defaults`, imperfection percentages,
  and boundary dates. Presets are defaults, not mandatory cross-domain values.
- `crm.json`: the engagement-focused CRM table contract, row targets, domain
  values, base-generation rules, business mappings, relationships,
  distribution selections and overrides, targets, imperfections, and join paths.

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
`crm.json`. Do not change `base.json` unless the reusable default itself should
change for every domain that inherits it.

Domain JSON is the source of truth for the domain contract, preset selections,
and overrides; `base.json` is the source for reusable defaults. Python config
validation checks required structure, DDL alignment, keys, relationships,
references, and value shape; it does not maintain a second hardcoded copy of
CRM tables, enums, join paths, or effective distribution parameters.

The `dev` profile is for local validation. The `full` profile is the only
profile permitted to write a versioned release.
